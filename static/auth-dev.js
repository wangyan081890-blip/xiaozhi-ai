/* ============================================================
   账号与 AI 开发中心（依赖 app.js 中的 escapeHtml/toast）
   ============================================================ */
const authState = { account: null, mode: "login", timer: null };

function authEls() {
    return {
        btn: document.getElementById("auth-btn"),
        devBtn: document.getElementById("dev-btn"),
        modal: document.getElementById("auth-modal"),
        close: document.getElementById("auth-close"),
        title: document.getElementById("auth-title"),
        tabLogin: document.getElementById("tab-login"),
        tabRegister: document.getElementById("tab-register"),
        form: document.getElementById("auth-form"),
        username: document.getElementById("auth-username"),
        password: document.getElementById("auth-password"),
        error: document.getElementById("auth-error"),
        submit: document.getElementById("auth-submit"),
        devModal: document.getElementById("dev-modal"),
        devClose: document.getElementById("dev-close"),
        devForm: document.getElementById("dev-form"),
        devTitle: document.getElementById("dev-title-input"),
        devReq: document.getElementById("dev-requirement"),
        devSubmit: document.getElementById("dev-submit"),
        devRefresh: document.getElementById("dev-refresh"),
        devStatus: document.getElementById("dev-status"),
        devList: document.getElementById("dev-list"),
        devNote: document.getElementById("dev-note"),
    };
}

async function apiJson(url, options = {}) {
    const res = await fetch(url, {
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        ...options,
    });
    let data = {};
    try {
        data = await res.json();
    } catch (_) {
        data = {};
    }
    if (!res.ok) {
        const detail = data.detail || `请求失败（${res.status}）`;
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
}

function setAuthButton() {
    const { btn, devBtn } = authEls();
    if (!btn) return;
    if (authState.account) {
        btn.textContent = authState.account.username;
        btn.classList.add("signed");
        btn.title = "账号中心（点击可退出登录）";
    } else {
        btn.textContent = "登录";
        btn.classList.remove("signed");
        btn.title = "登录 / 注册";
    }
    if (devBtn) devBtn.hidden = !(authState.account && authState.account.is_admin);
}

async function loadAuthStatus() {
    try {
        const data = await apiJson("/api/auth/status");
        authState.account = data.authenticated ? data.user : null;
    } catch (_) {
        authState.account = null;
    }
    setAuthButton();
}

function openAuthModal(mode) {
    const els = authEls();
    if (!els.modal) return;
    if (authState.account && mode === "login") {
        if (window.confirm(`当前登录：${authState.account.username}\n要退出登录吗？`)) {
            apiJson("/api/auth/logout", { method: "POST" })
                .then(() => location.reload())
                .catch((err) => toast(err.message));
        }
        return;
    }
    authState.mode = mode || "login";
    setAuthMode(authState.mode);
    els.error.hidden = true;
    els.modal.hidden = false;
    els.username.focus();
}

function setAuthMode(mode) {
    const els = authEls();
    authState.mode = mode;
    const registering = mode === "register";
    els.title.textContent = registering ? "注册小智账号" : "登录小智";
    els.submit.textContent = registering ? "注册并登录" : "登录";
    els.password.autocomplete = registering ? "new-password" : "current-password";
    els.tabLogin.classList.toggle("active", !registering);
    els.tabRegister.classList.toggle("active", registering);
}

async function submitAuth(event) {
    event.preventDefault();
    const els = authEls();
    const username = els.username.value.trim();
    const password = els.password.value;
    if (!username || !password) {
        els.error.textContent = "请填写用户名和密码";
        els.error.hidden = false;
        return;
    }
    els.submit.disabled = true;
    els.error.hidden = true;
    try {
        const endpoint = authState.mode === "register" ? "/api/auth/register" : "/api/auth/login";
        await apiJson(endpoint, {
            method: "POST",
            body: JSON.stringify({ username, password }),
        });
        toast(authState.mode === "register" ? "注册成功，已登录" : "登录成功");
        setTimeout(() => location.reload(), 350);
    } catch (err) {
        els.error.textContent = err.message;
        els.error.hidden = false;
    } finally {
        els.submit.disabled = false;
    }
}

function devStatusLabel(status) {
    const map = {
        queued: "排队中",
        dispatching: "启动代理",
        agent_running: "AI 改代码中",
        awaiting_approval: "等待你确认",
        deploying: "部署中",
        deployed: "已上线",
        failed: "失败",
        cancelled: "已取消",
        rejected: "已拒绝",
        rolled_back: "已回滚",
    };
    return map[status] || status;
}

function devStatusClass(status) {
    if (status === "failed" || status === "cancelled" || status === "rejected") return "failed";
    if (status === "deployed" || status === "rolled_back") return "done";
    return "";
}

function renderDevTasks(tasks) {
    const els = authEls();
    if (!els.devList) return;
    if (!tasks.length) {
        els.devList.innerHTML = '<div class="dev-empty">还没有任务。写下需求，AI 会先给出方案再改代码。</div>';
        return;
    }
    els.devList.innerHTML = tasks
        .map((task) => {
            const changed = (task.files_changed || []).join("、");
            const meta = [
                `创建：${(task.created_at || "").replace("T", " ").slice(0, 16)}`,
                task.branch ? `分支：${escapeHtml(task.branch)}` : "",
                task.pr_number ? `PR #${task.pr_number}` : "",
                task.test_summary ? `测试：${escapeHtml(String(task.test_summary).slice(0, 120))}` : "",
                changed ? `改动：${escapeHtml(changed.slice(0, 200))}` : "",
                task.error ? `错误：${escapeHtml(String(task.error).slice(0, 200))}` : "",
            ]
                .filter(Boolean)
                .join(" · ");
            const actions = [];
            if (task.status === "awaiting_approval") {
                actions.push(`<button class="ghost-btn" data-dev-approve="${escapeHtml(task.id)}">批准并部署</button>`);
                actions.push(`<button class="ghost-btn" data-dev-reject="${escapeHtml(task.id)}">拒绝</button>`);
            }
            if (task.status === "deployed") {
                actions.push(`<button class="ghost-btn" data-dev-rollback="${escapeHtml(task.id)}">回滚部署</button>`);
            }
            if (["queued", "agent_running", "awaiting_approval"].includes(task.status)) {
                actions.push(`<button class="ghost-btn" data-dev-cancel="${escapeHtml(task.id)}">取消</button>`);
            }
            const prLink = task.pr_url
                ? ` · <a href="${escapeHtml(task.pr_url)}" target="_blank" rel="noopener">查看 PR</a>`
                : "";
            return `<div class="dev-item">
                <div class="dev-item-top">
                    <span class="dev-item-title">${escapeHtml(task.title || "未命名任务")}</span>
                    <span class="dev-badge ${devStatusClass(task.status)}">${devStatusLabel(task.status)}</span>
                </div>
                <div class="dev-item-meta">${meta || "&nbsp;"}${prLink}</div>
                <div class="dev-item-actions">${actions.join("")}</div>
            </div>`;
        })
        .join("");
}

async function loadDevTasks(silent = false) {
    const els = authEls();
    if (!els.devList) return;
    try {
        const data = await apiJson("/api/dev/tasks?limit=30");
        renderDevTasks(data.tasks || []);
        if (!silent && els.devStatus) els.devStatus.textContent = "";
    } catch (err) {
        if (!silent && els.devList) els.devList.innerHTML = `<div class="dev-empty">${escapeHtml(err.message)}</div>`;
    }
}

async function openDevModal() {
    const els = authEls();
    if (!els.devModal) return;
    els.devModal.hidden = false;
    els.devStatus.textContent = "加载中…";
    try {
        const info = await apiJson("/api/dev/info");
        els.devNote.textContent = info.enabled
            ? "提交后 AI 会在 GitHub Actions 中改代码、跑测试并开 PR；你批准后才会部署到线上。"
            : "AI 开发代理尚未启用：需要配置 GitHub 与模型密钥后由管理员开启。";
        if (!info.enabled && els.devSubmit) els.devSubmit.disabled = true;
    } catch (err) {
        els.devStatus.textContent = err.message;
    }
    await loadDevTasks(true);
    els.devStatus.textContent = "";
    clearInterval(authState.timer);
    authState.timer = setInterval(() => {
        if (!els.devModal.hidden) loadDevTasks(true);
    }, 15000);
}

async function submitDevTask(event) {
    event.preventDefault();
    const els = authEls();
    const title = els.devTitle.value.trim();
    const requirement = els.devReq.value.trim();
    if (!requirement) {
        els.devStatus.textContent = "请填写需求描述";
        return;
    }
    els.devSubmit.disabled = true;
    els.devStatus.textContent = "提交中…";
    try {
        await apiJson("/api/dev/tasks", {
            method: "POST",
            body: JSON.stringify({ title, requirement }),
        });
        els.devReq.value = "";
        els.devTitle.value = "";
        els.devStatus.textContent = "已提交，AI 将开始处理";
        await loadDevTasks(true);
    } catch (err) {
        els.devStatus.textContent = err.message;
    } finally {
        els.devSubmit.disabled = false;
    }
}

async function devTaskAction(taskId, action) {
    const els = authEls();
    const labels = { approve: "批准", reject: "拒绝", rollback: "回滚", cancel: "取消" };
    if (action === "approve") {
        if (!window.confirm("批准后会自动合并 PR 并部署到线上，确定继续吗？")) return;
    } else if (!window.confirm(`确定要${labels[action] || action}这个任务吗？`)) {
        return;
    }
    try {
        const endpoint =
            action === "approve"
                ? `/api/dev/tasks/${taskId}/approve`
                : `/api/dev/tasks/${taskId}/${action}`;
        const body = action === "approve" ? { action: "merge", note: "" } : undefined;
        await apiJson(endpoint, { method: "POST", body: body ? JSON.stringify(body) : undefined });
        toast(`已${labels[action] || action}`);
        await loadDevTasks(true);
    } catch (err) {
        toast(err.message);
    }
}

function setupAuthUI() {
    const els = authEls();
    if (!els.btn) return;
    els.btn.addEventListener("click", () => openAuthModal("login"));
    if (els.close) els.close.addEventListener("click", () => (els.modal.hidden = true));
    if (els.modal) {
        els.modal.addEventListener("click", (event) => {
            if (event.target === els.modal) els.modal.hidden = true;
        });
    }
    if (els.tabLogin) els.tabLogin.addEventListener("click", () => setAuthMode("login"));
    if (els.tabRegister) els.tabRegister.addEventListener("click", () => setAuthMode("register"));
    if (els.form) els.form.addEventListener("submit", submitAuth);

    if (els.devBtn) els.devBtn.addEventListener("click", openDevModal);
    if (els.devClose) {
        els.devClose.addEventListener("click", () => {
            els.devModal.hidden = true;
            clearInterval(authState.timer);
        });
    }
    if (els.devModal) {
        els.devModal.addEventListener("click", (event) => {
            if (event.target === els.devModal) {
                els.devModal.hidden = true;
                clearInterval(authState.timer);
            }
        });
    }
    if (els.devForm) els.devForm.addEventListener("submit", submitDevTask);
    if (els.devRefresh) els.devRefresh.addEventListener("click", () => loadDevTasks(false));
    if (els.devList) {
        els.devList.addEventListener("click", (event) => {
            const button = event.target.closest(
                "button[data-dev-approve],button[data-dev-reject],button[data-dev-rollback],button[data-dev-cancel]"
            );
            if (!button) return;
            if (button.dataset.devApprove) devTaskAction(button.dataset.devApprove, "approve");
            else if (button.dataset.devReject) devTaskAction(button.dataset.devReject, "reject");
            else if (button.dataset.devRollback) devTaskAction(button.dataset.devRollback, "rollback");
            else if (button.dataset.devCancel) devTaskAction(button.dataset.devCancel, "cancel");
        });
    }

    loadAuthStatus();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setupAuthUI);
} else {
    setupAuthUI();
}
