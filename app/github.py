"""GitHub REST 客户端：派发 workflow、读取运行状态、合并/关闭 PR。

只使用标准库 urllib，避免在 Python 3.14 上引入额外依赖。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from app.config import GITHUB_API, GITHUB_BRANCH, GITHUB_REPO, GITHUB_TOKEN


class GitHubError(RuntimeError):
    pass


def _request(method: str, path: str, payload: dict | None = None, timeout: int = 60) -> dict:
    if not GITHUB_TOKEN or not GITHUB_REPO:
        raise GitHubError("GitHub 未配置（GITHUB_REPO / GITHUB_TOKEN）")
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{GITHUB_API}{path}",
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "xiaozhi-ai-control-plane",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise GitHubError(f"GitHub {method} {path} -> {exc.code}: {detail}") from exc


def repo_ready() -> bool:
    try:
        info = _request("GET", f"/repos/{GITHUB_REPO}")
        return bool(info.get("default_branch"))
    except (GitHubError, urllib.error.URLError):
        return False


def get_branch_head(branch: str | None = None) -> str:
    branch = branch or GITHUB_BRANCH
    data = _request("GET", f"/repos/{GITHUB_REPO}/git/ref/heads/{branch}")
    return data["object"]["sha"]


def dispatch_workflow(workflow: str, ref: str, inputs: dict) -> int:
    """派发 workflow_dispatch，返回该次 run 的 id（轮询匹配，最多 30 秒）。"""
    started = time.time()
    _request("POST", f"/repos/{GITHUB_REPO}/actions/workflows/{workflow}/dispatches", {"ref": ref, "inputs": inputs})
    deadline = started + 45
    while time.time() < deadline:
        runs = _request("GET", f"/repos/{GITHUB_REPO}/actions/workflows/{workflow}/runs?per_page=5")
        for run in runs.get("workflow_runs", []):
            created = run.get("created_at", "")
            if run.get("event") == "workflow_dispatch" and created >= time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(started - 5)
            ):
                return int(run["id"])
        time.sleep(3)
    raise GitHubError("workflow 已派发，但 45 秒内没有找到对应的运行记录")


def get_run(run_id: int) -> dict:
    return _request("GET", f"/repos/{GITHUB_REPO}/actions/runs/{run_id}")


def cancel_run(run_id: int) -> None:
    _request("POST", f"/repos/{GITHUB_REPO}/actions/runs/{run_id}/cancel")


def get_pull(number: int) -> dict:
    return _request("GET", f"/repos/{GITHUB_REPO}/pulls/{number}")


def merge_pull(number: int, sha: str | None = None, message: str = "") -> dict:
    payload: dict = {"merge_method": "squash"}
    if sha:
        payload["sha"] = sha
    if message:
        payload["commit_message"] = message
    return _request("PUT", f"/repos/{GITHUB_REPO}/pulls/{number}/merge", payload)


def close_pull(number: int) -> dict:
    return _request("PATCH", f"/repos/{GITHUB_REPO}/pulls/{number}", {"state": "closed"})


def repo_slug() -> str:
    return GITHUB_REPO
