"""AI 开发代理（在 GitHub Actions 的隔离环境中运行）。

工作方式：调用 DeepSeek，按 JSON 指令使用受限工具读取/修改 app/ 与 static/，
运行 pytest 与前端语法检查，全部通过后创建分支、提交、开 PR，并回调云端应用。

安全约束见 app/devguard.py：不能改测试、CI、Docker、依赖与密钥文件。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import posixpath
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import devguard  # noqa: E402
from app.redact import redact  # noqa: E402

API_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/") + "/chat/completions"
MODEL = os.environ.get("AGENT_MODEL", "deepseek-chat")
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "18"))
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "4096"))

SYSTEM_PROMPT = """你是「小智 AI」的自主开发代理，在 GitHub Actions 中为这个 FastAPI + 原生前端项目实现需求。

规则（必须遵守）：
1. 只能读取/修改 app/、static/、docs/、README.md；不能碰 tests/、.github/、Dockerfile、requirements、config.env、.env。
2. 一次任务最多改 {max_files} 个文件，每个文件不超过 {max_bytes} 字节。
3. 不要输出任何密钥；不要伪造测试结果。
4. 修改后必须运行 run_tests；若失败，读取错误并修复，直到通过或步数耗尽。
5. 保持现有代码风格：Python 3.12、类型注解、中文注释/文案；前端为原生 JS/CSS。
6. 每次只输出一个 JSON 对象，不要 Markdown 代码块，不要多余文字。
7. read_file 会返回完整文件内容（超过 60000 字节才会被截断，并在结果里注明）；不要假设只能读到文件尾部。
8. 修改文案/删除文案必须直接改源文件内容；禁止用 `display:none`、覆盖样式、隐藏元素等方式“绕过”需求。
9. 如果需求涉及新依赖、外部服务或超大改造，先确认能否在允许目录内完成；不能完成时用 finish 说明原因，不要静默结束。

可用工具（action）：
- list_files: 列出仓库文件（无需 path）
- read_file: 读取文件，path 必填
- write_file: 写入完整文件内容，path/content 必填（整文件覆盖）
- delete_file: 删除文件，path 必填（谨慎）
- run_tests: 运行 pytest + node --check，返回通过/失败与日志
- finish: 结束任务，summary 必填（中文，说明改了什么、如何验证）

JSON 示例：{{"thought":"先看入口","action":"read_file","path":"app/main.py"}}
输出必须是合法 JSON 对象。当前任务：{task_id}
"""


def log(message: str) -> None:
    print(f"[agent] {message}", flush=True)


def llm(messages: list[dict]) -> str:
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": MAX_TOKENS,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload).encode()
    last_error: Exception | None = None
    for attempt in range(3):
        request = urllib.request.Request(
            API_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {os.environ.get('DEEPSEEK_API_KEY', '')}",
                "User-Agent": "xiaozhi-dev-agent",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                body = json.loads(response.read())
            return body["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"调用模型失败：{last_error}")


def parse_action(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = text.rstrip("`").strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("模型没有输出 JSON")
    return json.loads(match.group(0))


def run(command: list[str], timeout: int = 900, env: dict | None = None) -> tuple[int, str]:
    merged = os.environ.copy()
    merged.update({"GIT_TERMINAL_PROMPT": "0", "PYTHONIOENCODING": "utf-8"})
    if env:
        merged.update(env)
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=merged,
        )
        return completed.returncode, (completed.stdout or "") + (completed.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"命令超时：{' '.join(command)}"


def run_tests() -> tuple[bool, str]:
    code, py_out = run(
        [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"], timeout=600
    )
    parts = ["== pytest ==", py_out[-6000:]]
    node = os.environ.get("NODE_BIN", "node")
    check_code, node_out = run([node, "--check", "static/app.js"], timeout=120)
    parts += ["== node --check static/app.js ==", node_out[-2000:]]
    ok = code == 0 and check_code == 0
    return ok, "\n".join(parts)


def git(args: list[str]) -> tuple[int, str]:
    return run(["git", *args], timeout=180)


def changed_files(base_ref: str = "origin/main") -> list[str]:
    code, out = git(["diff", "--name-only", f"{base_ref}...HEAD"])
    if code != 0:
        code, out = git(["status", "--porcelain"])
        return [line[3:].strip() for line in out.splitlines() if len(line) > 3]
    return [line.strip() for line in out.splitlines() if line.strip()]


def callback(task_id: str, payload: dict) -> None:
    base = os.environ.get("APP_BASE_URL", "").rstrip("/")
    secret = os.environ.get("DEV_CALLBACK_SECRET", "")
    if not base or not secret:
        log("缺少 APP_BASE_URL / DEV_CALLBACK_SECRET，跳过回调")
        return
    body = json.dumps({"task_id": task_id, **payload}, ensure_ascii=False).encode()
    import hashlib
    import hmac

    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    for attempt in range(4):
        request = urllib.request.Request(
            f"{base}/api/dev/callback",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Dev-Signature": signature,
                "User-Agent": "xiaozhi-dev-agent",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                if response.status < 300:
                    log("回调成功")
                    return
        except Exception as exc:  # noqa: BLE001
            log(f"回调失败（第 {attempt + 1} 次）：{exc}")
        time.sleep(5 * (attempt + 1))
    log("回调最终失败")


def create_pr(title: str, body: str, branch: str) -> tuple[int, str]:
    token = os.environ.get("GH_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    payload = json.dumps({"title": title, "head": branch, "base": "main", "body": body}).encode()
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/pulls",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "xiaozhi-dev-agent",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read())
    return int(data["number"]), str(data["html_url"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--branch", required=True)
    args = parser.parse_args()

    requirement_b64 = os.environ.get("REQUIREMENT_B64", "")
    requirement = base64.b64decode(requirement_b64).decode("utf-8") if requirement_b64 else ""
    if not requirement:
        callback(args.task_id, {"status": "failed", "error": "缺少需求内容"})
        return 1

    files = devguard.repo_files(ROOT)
    system = SYSTEM_PROMPT.format(
        max_files=devguard.MAX_FILES,
        max_bytes=devguard.MAX_FILE_BYTES,
        task_id=args.task_id,
    )
    system += "\n仓库文件（最多 400 个）：\n" + "\n".join(files[:400])
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": "需求：\n" + requirement},
    ]

    events: list[tuple[str, str]] = []
    changed: set[str] = set()
    plan = ""
    summary = ""
    error = ""
    tests_ok = False
    test_summary = ""

    for step in range(1, MAX_STEPS + 1):
        try:
            raw = llm(messages)
            action = parse_action(raw)
        except Exception as exc:  # noqa: BLE001
            error = f"模型输出无法解析：{exc}"
            events.append(("error", error))
            break
        name = str(action.get("action") or "")
        messages.append({"role": "assistant", "content": raw})
        log(f"step {step}: {name}")
        try:
            if name == "list_files":
                observation = "\n".join(files)
            elif name == "read_file":
                path = devguard.safe_path(action.get("path", ""))
                if not devguard.is_allowed_read(path):
                    raise ValueError("该路径不允许读取")
                with open(os.path.join(ROOT, path), encoding="utf-8") as handle:
                    content = handle.read()
                if len(content) > 60000:
                    observation = content[:60000] + "\n\n[内容过长，仅显示前 60000 字符]"
                else:
                    observation = content
            elif name == "write_file":
                path = devguard.safe_path(action.get("path", ""))
                if not devguard.is_allowed_write(path):
                    raise ValueError("该路径不允许写入（测试/CI/依赖/配置为只读）")
                content = action.get("content")
                if not isinstance(content, str):
                    raise ValueError("content 必须是字符串")
                devguard.check_content_size(content)
                if devguard.looks_like_secret(content):
                    raise ValueError("内容中疑似包含密钥，已拒绝写入")
                changed.add(path)
                devguard.check_file_count(sorted(changed))
                full = os.path.join(ROOT, path)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(content)
                observation = f"已写入 {path}（{len(content)} 字符）"
            elif name == "delete_file":
                path = devguard.safe_path(action.get("path", ""))
                if not devguard.is_allowed_write(path):
                    raise ValueError("该路径不允许删除")
                full = os.path.join(ROOT, path)
                if os.path.exists(full):
                    os.remove(full)
                    changed.add(path)
                observation = f"已删除 {path}"
            elif name == "run_tests":
                tests_ok, test_summary = run_tests()
                observation = test_summary[-8000:]
            elif name == "finish":
                summary = str(action.get("summary") or "")[:4000]
                plan = str(action.get("plan") or plan)[:4000]
                events.append(("info", f"代理结束：{summary or '无摘要'}"))
                break
            else:
                raise ValueError(f"未知工具：{name}")
        except Exception as exc:  # noqa: BLE001
            observation = f"错误：{exc}"
            events.append(("warn", observation))
        messages.append({"role": "user", "content": "工具结果：\n" + observation[-30000:]})
        if len(messages) > 44:
            messages = messages[:2] + messages[-40:]

    if not changed:
        error = error or "代理没有修改任何文件"
        callback(args.task_id, {"status": "failed", "error": error, "events": events})
        return 1

    tests_ok, test_summary = run_tests()
    if not tests_ok:
        error = "自动测试未通过，禁止创建 PR/部署"
        callback(
            args.task_id,
            {"status": "failed", "error": error, "summary": summary, "test_summary": test_summary[-6000:], "events": events},
        )
        return 1
    if devguard.looks_like_secret(test_summary):
        test_summary = redact(test_summary)

    # 提交并推送分支
    git(["config", "user.name", "xiaozhi-dev-agent"])
    git(["config", "user.email", "agent@users.noreply.github.com"])
    git(["checkout", "-B", args.branch])
    code, out = git(["add", "-A"])
    if code != 0:
        callback(args.task_id, {"status": "failed", "error": f"git add 失败：{out}"})
        return 1
    code, out = git(["commit", "-m", f"AI 开发：{summary[:60] or args.task_id[:8]}"])
    if code != 0 and "nothing to commit" not in out:
        callback(args.task_id, {"status": "failed", "error": f"git commit 失败：{out}"})
        return 1

    token = os.environ.get("GH_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    git(["remote", "set-url", "origin", f"https://x-access-token:{token}@github.com/{repo}.git"])
    code, out = git(["push", "-f", "origin", args.branch])
    if code != 0:
        callback(args.task_id, {"status": "failed", "error": f"git push 失败：{out[-800:]}"})
        return 1

    # 与 main 比较出实际改动文件
    base = os.environ.get("GITHUB_BASE", "main")
    git(["fetch", "origin", base])
    changed_list = changed_files(f"origin/{base}")

    files_text = ", ".join(changed_list)[:2000]
    body = (
        f"### 任务 {args.task_id}\n\n**需求**：{requirement[:1500]}\n\n"
        f"**AI 摘要**：{summary}\n\n**改动文件**：{files_text}\n\n"
        f"**自动测试**：通过（pytest + node --check）\n\n"
        f"> 本 PR 由 AI 开发代理生成，合并前请人工审批。合并后自动触发部署 workflow。"
    )
    try:
        pr_number, pr_url = create_pr(f"AI 开发：{summary[:40] or args.task_id[:8]}", body, args.branch)
    except Exception as exc:  # noqa: BLE001
        callback(args.task_id, {"status": "failed", "error": f"创建 PR 失败：{exc}", "test_summary": test_summary[-3000:], "events": events})
        return 1

    head_code, head_out = git(["rev-parse", "HEAD"])
    commit_sha = head_out.strip() if head_code == 0 else ""
    callback(
        args.task_id,
        {
            "status": "success",
            "plan": plan,
            "summary": summary,
            "test_summary": test_summary[-4000:],
            "branch": args.branch,
            "pr_number": pr_number,
            "pr_url": pr_url,
            "commit_sha": commit_sha,
            "files_changed": changed_list,
            "events": events[-30:],
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
