"""AI 自主开发任务编排：派发 GitHub Actions、审批、部署与回滚。

安全边界：
- 只有管理员能创建/审批/回滚；
- 生产容器不执行 shell、不接触仓库代码；真正的改代码发生在 GitHub Actions；
- 回调都带 HMAC 签名，agent 无法伪造审批或部署结果。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging

from app import db, github, railway
from app.config import (
    AGENT_ENABLED,
    AGENT_WORKFLOW,
    APP_BASE_URL,
    DEPLOY_WORKFLOW,
    DEV_CALLBACK_SECRET,
    GITHUB_BRANCH,
    GITHUB_REPO,
    RAILWAY_PUBLIC_URL,
    RAILWAY_SERVICE_ID,
)
from app.redact import redact

log = logging.getLogger("xiaozhi.dev")

ACTIVE_STATUSES = {"queued", "planning", "editing", "testing", "deploying"}


class DevError(RuntimeError):
    pass


def configured() -> bool:
    return bool(AGENT_ENABLED and GITHUB_REPO and DEV_CALLBACK_SECRET and APP_BASE_URL)


def _event(task_id: str, level: str, message: str) -> None:
    db.add_dev_event(task_id, level, redact(message))


def create_task(user_id: str, title: str, requirement: str) -> dict:
    if not AGENT_ENABLED:
        raise DevError("AI 开发代理未启用（AGENT_ENABLED=false）")
    if not configured():
        raise DevError("缺少 GitHub / 回调配置，无法派发开发任务")
    requirement = requirement.strip()
    if len(requirement) < 8:
        raise DevError("需求描述太短")
    task = db.create_dev_task(user_id, title or requirement[:24], requirement)
    branch = f"ai/task-{task['id'][:8]}"
    try:
        run_id = github.dispatch_workflow(
            AGENT_WORKFLOW,
            GITHUB_BRANCH,
            {
                "task_id": task["id"],
                "requirement_b64": base64.b64encode(requirement.encode()).decode(),
                "branch": branch,
            },
        )
    except Exception as exc:  # noqa: BLE001 - 需要把失败反馈到任务
        db.update_dev_task(task["id"], status="failed", error=redact(str(exc)), branch=branch)
        _event(task["id"], "error", f"派发开发任务失败：{exc}")
        raise DevError(f"派发开发任务失败：{exc}") from exc
    db.update_dev_task(task["id"], status="running", branch=branch)
    _event(task["id"], "info", f"已派发 GitHub Actions（run {run_id}），AI 正在分析项目")
    return db.get_dev_task(task["id"]) or task


def cancel_task(task_id: str) -> dict:
    task = db.get_dev_task(task_id)
    if not task:
        raise DevError("任务不存在")
    if task["status"] not in ACTIVE_STATUSES:
        raise DevError("该任务当前不可取消")
    db.update_dev_task(task_id, status="cancelled")
    _event(task_id, "warn", "管理员已终止任务；后续回调会被忽略")
    return db.get_dev_task(task_id) or task


def approve(task_id: str, action: str, note: str = "") -> dict:
    task = db.get_dev_task(task_id)
    if not task:
        raise DevError("任务不存在")
    if task["status"] != "awaiting_approval":
        raise DevError("该任务不在待审批状态")
    pr_number = task.get("pr_number")
    if not pr_number:
        raise DevError("任务缺少 PR 编号，无法审批")
    if action == "reject":
        try:
            github.close_pull(int(pr_number))
        except Exception as exc:  # noqa: BLE001
            _event(task_id, "warn", f"关闭 PR 失败（可手动关闭）：{exc}")
        db.update_dev_task(task_id, status="rejected")
        _event(task_id, "info", redact(f"管理员拒绝了本次改动。{note}"))
        return db.get_dev_task(task_id) or task

    try:
        merged = github.merge_pull(int(pr_number), message=f"小智 AI 开发任务 {task_id[:8]}：{task.get('title')}")
    except Exception as exc:  # noqa: BLE001
        db.update_dev_task(task_id, status="failed", error=redact(str(exc)))
        _event(task_id, "error", f"合并 PR 失败：{exc}")
        raise DevError(f"合并 PR 失败：{exc}") from exc
    commit_sha = merged.get("sha") or ""
    db.update_dev_task(task_id, status="deploying", commit_sha=commit_sha)
    _event(task_id, "info", f"已合并到 {GITHUB_BRANCH}（{commit_sha[:8]}），开始部署")
    try:
        run_id = github.dispatch_workflow(
            DEPLOY_WORKFLOW,
            GITHUB_BRANCH,
            {"task_id": task_id, "commit_sha": commit_sha},
        )
        _event(task_id, "info", f"已派发部署 workflow（run {run_id}）：测试 → 构建 → 部署 → 健康检查")
    except Exception as exc:  # noqa: BLE001
        db.update_dev_task(task_id, status="failed", error=redact(str(exc)))
        _event(task_id, "error", f"派发部署失败：{exc}")
        raise DevError(f"派发部署失败：{exc}") from exc
    return db.get_dev_task(task_id) or task


def _previous_success(current_id: str | None = None) -> dict | None:
    try:
        items = railway.list_deployments(10)
    except railway.RailwayError as exc:
        log.warning("读取 Railway 部署失败：%s", exc)
        return None
    for item in items:
        if item["status"] == "SUCCESS" and item["id"] != current_id:
            return item
    return None


def rollback(task_id: str, note: str = "") -> dict:
    task = db.get_dev_task(task_id)
    if not task:
        raise DevError("任务不存在")
    previous = _previous_success()
    if not previous:
        raise DevError("没有可回滚的历史成功部署")
    if not railway.redeploy(previous["id"]):
        raise DevError("Railway 回滚请求未被接受")
    db.update_dev_task(task_id, status="rolled_back")
    db.add_dev_version(task_id, task.get("commit_sha"), previous["id"], "rolled_back", redact(note))
    _event(task_id, "warn", f"已回滚到部署 {previous['id'][:8]}（{previous.get('created_at')}）。{note}")
    return db.get_dev_task(task_id) or task


def verify_signature(body: bytes, signature: str | None) -> bool:
    if not DEV_CALLBACK_SECRET or not signature:
        return False
    expected = hmac.new(DEV_CALLBACK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def handle_agent_callback(payload: dict) -> None:
    task_id = str(payload.get("task_id") or "")
    task = db.get_dev_task(task_id)
    if not task:
        raise DevError("未知任务")
    if task["status"] in {"cancelled", "rejected", "rolled_back"}:
        _event(task_id, "warn", "任务已终止，忽略迟到的代理回调")
        return
    status = str(payload.get("status") or "")
    plan = (payload.get("plan") or "")[:8000]
    summary = (payload.get("summary") or "")[:8000]
    test_summary = (payload.get("test_summary") or "")[:8000]
    files = payload.get("files_changed") or []
    if isinstance(files, list):
        files_text = ", ".join(str(item) for item in files)[:4000]
    else:
        files_text = str(files)[:4000]
    for level, message in (payload.get("events") or [])[-40:]:
        _event(task_id, str(level), str(message))

    if status == "success" and payload.get("pr_number"):
        db.update_dev_task(
            task_id,
            status="awaiting_approval",
            plan=plan,
            summary=summary,
            test_summary=test_summary,
            files_changed=files_text,
            pr_number=int(payload["pr_number"]),
            pr_url=str(payload.get("pr_url") or ""),
            branch=str(payload.get("branch") or task.get("branch") or ""),
            commit_sha=str(payload.get("commit_sha") or ""),
        )
        _event(task_id, "info", "AI 已完成改动并通过测试，等待管理员审批")
    else:
        db.update_dev_task(
            task_id,
            status="failed",
            plan=plan,
            summary=summary,
            test_summary=test_summary,
            files_changed=files_text,
            error=redact(str(payload.get("error") or "AI 代理执行失败")),
        )
        _event(task_id, "error", redact(str(payload.get("error") or "AI 代理执行失败")))


def handle_deploy_callback(payload: dict) -> None:
    task_id = str(payload.get("task_id") or "")
    task = db.get_dev_task(task_id)
    if not task:
        raise DevError("未知任务")
    status = str(payload.get("status") or "")
    deployment_id = str(payload.get("deployment_id") or "")
    if status == "success":
        db.update_dev_task(task_id, status="deployed")
        db.add_dev_version(task_id, task.get("commit_sha"), deployment_id or None, "deployed", "部署成功")
        _event(task_id, "info", "部署成功，健康检查通过，新版本已上线")
    else:
        error = redact(str(payload.get("error") or "部署失败"))
        db.update_dev_task(task_id, status="failed", error=error)
        _event(task_id, "error", error)
        # 部署失败自动回滚到上一个成功版本
        try:
            previous = _previous_success(deployment_id)
            if previous and railway.redeploy(previous["id"]):
                db.update_dev_task(task_id, status="rolled_back")
                db.add_dev_version(task_id, task.get("commit_sha"), previous["id"], "rolled_back", "部署失败自动回滚")
                _event(task_id, "warn", f"已自动回滚到部署 {previous['id'][:8]}")
        except Exception as exc:  # noqa: BLE001
            _event(task_id, "error", f"自动回滚失败，请手动处理：{exc}")


def public_url() -> str:
    return RAILWAY_PUBLIC_URL


def info() -> dict:
    return {
        "enabled": AGENT_ENABLED,
        "configured": configured(),
        "repo": GITHUB_REPO,
        "branch": GITHUB_BRANCH,
        "service": RAILWAY_SERVICE_ID,
        "public_url": RAILWAY_PUBLIC_URL,
    }
