/* ============================================================
   小智 AI —— 前端逻辑
   纯原生 JS：SSE 流式对话、Markdown 渲染、多会话、主题切换
   ============================================================ */
"use strict";

window.__xzBuild = "20261118d";
console.info("[小智] build", window.__xzBuild);

const $ = (id) => document.getElementById(id);

const els = {
    app: $("app"),
    sidebar: $("sidebar"),
    backdrop: $("backdrop"),
    menuBtn: $("menu-btn"),
    newChat: $("new-chat"),
    historyList: $("history-list"),
    chatBox: $("chat-box"),
    welcome: $("welcome"),
    chatTitle: $("chat-title"),
    statusDot: $("status-dot"),
    statusText: $("status-text"),
    banner: $("banner"),
    bannerText: $("banner-text"),
    bannerBtn: $("banner-btn"),
    modelSelect: $("model-select"),
    thinkWrap: $("think-wrap"),
    thinkToggle: $("think-toggle"),
    themeBtn: $("theme-btn"),
    message: $("message"),
    sendBtn: $("send-btn"),
    toast: $("toast"),
};

const state = {
    sessionId: localStorage.getItem("xz_session") || newSessionId(),
    messages: [],
    model: localStorage.getItem("xz_model") || "",
    think: localStorage.getItem("xz_think") === "1",
    generating: false,
    controller: null,
    modelThinking: false,
    modelProviders: {},
    defaultProvider: "",
    title: "新对话",
};

/* ------------------------------------------------------------------ */
/* 工具函数                                                           */
/* ------------------------------------------------------------------ */
function newSessionId() {
    const raw =
        (crypto.randomUUID && crypto.randomUUID()) ||
        String(Date.now()) + Math.random().toString(16).slice(2);
    return raw.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 24);
}

function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (ch) => {
        switch (ch) {
            case "&": return "&amp;";
            case "<": return "&lt;";
            case ">": return "&gt;";
            case '"': return "&quot;";
            default: return "&#39;";
        }
    });
}

function toast(message) {
    els.toast.textContent = message;
    els.toast.classList.add("show");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => els.toast.classList.remove("show"), 1900);
}

async function copyText(text) {
    try {
        await navigator.clipboard.writeText(text);
        toast("已复制到剪贴板");
    } catch (_) {
        const area = document.createElement("textarea");
        area.value = text;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        try { document.execCommand("copy"); toast("已复制到剪贴板"); }
        catch (e) { toast("复制失败，请手动选择文本"); }
        area.remove();
    }
}

/* ------------------------------------------------------------------ */
/* Markdown 渲染（先转义，再解析，避免 XSS）                          */
/* ------------------------------------------------------------------ */
function renderMarkdown(source) {
    if (!source) return "";

    const codeBlocks = [];
    const inlineCodes = [];

    // 1. 先抽出围栏代码块（保留原始内容，稍后单独转义）
    let text = String(source).replace(
        /```([^\n`]*)\n?([\s\S]*?)(?:```|$)/g,
        (_match, info, code) => {
            const lang = (info || "").trim().split(/\s+/)[0] || "text";
            codeBlocks.push({ lang, code: code.replace(/\n$/, "") });
            return `\u0000CB${codeBlocks.length - 1}\u0000`;
        }
    );

    // 2. 整体转义
    text = escapeHtml(text);

    // 3. 行内代码
    text = text.replace(/`([^`\n]+)`/g, (_match, code) => {
        inlineCodes.push(code);
        return `\u0000IC${inlineCodes.length - 1}\u0000`;
    });

    // 4. 链接（仅允许安全协议）
    text = text.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (match, label, url) => {
        const decoded = url.replace(/&amp;/g, "&");
        if (!/^(https?:\/\/|mailto:|\/|#)/i.test(decoded)) return match;
        return `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    });

    // 5. 粗体 / 斜体 / 删除线
    text = text
        .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
        .replace(/__([^_\n]+)__/g, "<strong>$1</strong>")
        .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>")
        .replace(/~~([^~\n]+)~~/g, "<del>$1</del>");

    // 6. 逐行解析块级元素
    const lines = text.split("\n");
    const out = [];
    let paragraph = [];
    let list = null; // { type: "ul" | "ol", items: [] }

    const flushParagraph = () => {
        if (paragraph.length) {
            out.push(`<p>${paragraph.join("<br>")}</p>`);
            paragraph = [];
        }
    };
    const flushList = () => {
        if (!list) return;
        const items = list.items.map((item) => `<li>${item}</li>`).join("");
        out.push(list.type === "ul" ? `<ul>${items}</ul>` : `<ol>${items}</ol>`);
        list = null;
    };

    for (const rawLine of lines) {
        const line = rawLine.trim();

        if (!line) {
            flushParagraph();
            flushList();
            continue;
        }

        if (/^\u0000CB\d+\u0000$/.test(line)) {
            flushParagraph();
            flushList();
            out.push(line);
            continue;
        }

        let match = line.match(/^(#{1,4})\s+(.+)$/);
        if (match) {
            flushParagraph();
            flushList();
            const level = match[1].length;
            out.push(`<h${level}>${match[2]}</h${level}>`);
            continue;
        }

        if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) {
            flushParagraph();
            flushList();
            out.push("<hr>");
            continue;
        }

        match = line.match(/^>\s?(.*)$/);
        if (match) {
            flushParagraph();
            flushList();
            out.push(`<blockquote>${match[1]}</blockquote>`);
            continue;
        }

        match = line.match(/^[-*+]\s+(.+)$/);
        if (match) {
            flushParagraph();
            if (!list || list.type !== "ul") {
                flushList();
                list = { type: "ul", items: [] };
            }
            list.items.push(match[1]);
            continue;
        }

        match = line.match(/^\d+[.)]\s+(.+)$/);
        if (match) {
            flushParagraph();
            if (!list || list.type !== "ol") {
                flushList();
                list = { type: "ol", items: [] };
            }
            list.items.push(match[1]);
            continue;
        }

        flushList();
        paragraph.push(line);
    }

    flushParagraph();
    flushList();

    // 7. 还原代码块与行内代码
    return out
        .join("\n")
        .replace(/\u0000CB(\d+)\u0000/g, (_match, index) => {
            const block = codeBlocks[Number(index)];
            if (!block) return "";
            const lang = block.lang.replace(/[^\w+#.-]/g, "") || "text";
            return (
                `<div class="code-block">` +
                `<div class="code-head"><span>${escapeHtml(lang)}</span>` +
                `<button class="code-copy" type="button">复制</button></div>` +
                `<pre><code class="language-${lang}">${escapeHtml(block.code)}</code></pre>` +
                `</div>`
            );
        })
        .replace(/\u0000IC(\d+)\u0000/g, (_match, index) => {
            const code = inlineCodes[Number(index)];
            return code === undefined ? "" : `<code>${code}</code>`;
        });
}

/* ------------------------------------------------------------------ */
/* 消息渲染                                                           */
/* ------------------------------------------------------------------ */
const AVATAR_USER = "我";
const AVATAR_BOT = "智";

function hideWelcome() {
    if (els.welcome) els.welcome.style.display = "none";
}

function showWelcome() {
    if (els.welcome) els.welcome.style.display = "";
}

function nearBottom() {
    return els.chatBox.scrollHeight - els.chatBox.scrollTop - els.chatBox.clientHeight < 140;
}

function scrollToBottom(force = false) {
    if (force || nearBottom()) {
        els.chatBox.scrollTop = els.chatBox.scrollHeight;
    }
}

function createRow(role, content) {
    const row = document.createElement("div");
    row.className = `message-row ${role}`;

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = role === "user" ? AVATAR_USER : AVATAR_BOT;

    const wrap = document.createElement("div");
    wrap.style.minWidth = "0";

    const bubble = document.createElement("div");
    bubble.className = "bubble";

    const tools = document.createElement("div");
    tools.className = "message-tools";

    if (role === "user") {
        bubble.textContent = content;
        row.dataset.raw = content;
    } else {
        bubble.innerHTML = renderMarkdown(content);
        row.dataset.raw = content;

        const copyBtn = document.createElement("button");
        copyBtn.type = "button";
        copyBtn.className = "tool-btn copy-btn";
        copyBtn.innerHTML =
            '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M5 15V6a2 2 0 0 1 2-2h9" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>复制';
        tools.appendChild(copyBtn);

        const regenBtn = document.createElement("button");
        regenBtn.type = "button";
        regenBtn.className = "tool-btn regen-btn";
        regenBtn.innerHTML =
            '<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>重新生成';
        tools.appendChild(regenBtn);
    }

    wrap.appendChild(bubble);
    wrap.appendChild(tools);
    row.appendChild(avatar);
    row.appendChild(wrap);

    hideWelcome();
    els.chatBox.appendChild(row);
    scrollToBottom(true);
    return row;
}

function createAssistantPlaceholder() {
    const row = createRow("assistant", "");
    const bubble = row.querySelector(".bubble");
    bubble.innerHTML =
        '<span class="typing"><span></span><span></span><span></span></span>';
    row.classList.add("waiting");
    return row;
}

function updateAssistantRow(row, text, thinking, streaming) {
    const bubble = row.querySelector(".bubble");
    let html = "";

    if (thinking) {
        html +=
            '<details class="thinking"' +
            (streaming ? " open" : "") +
            "><summary>思考过程</summary>" +
            `<div class="thinking-body">${escapeHtml(thinking)}</div></details>`;
    }

    html += renderMarkdown(text);

    if (!html) {
        html =
            '<span class="typing"><span></span><span></span><span></span></span>';
    }

    bubble.innerHTML = html;
    if (text) {
        bubble.classList.add("has-content");
    }
    row.dataset.raw = text;
}

function setAssistantMeta(row, text, stats) {
    if (!text) return;
    const meta = document.createElement("div");
    meta.className = "message-meta";

    const parts = [];
    parts.push(stats && stats.eval_count ? "本地推理" : "在线推理");
    if (stats && stats.eval_count && stats.total_duration_ms) {
        const speed = stats.eval_count / (stats.total_duration_ms / 1000);
        parts.push(`${speed.toFixed(1)} tok/s`);
        parts.push(`${(stats.total_duration_ms / 1000).toFixed(1)} 秒`);
    }
    parts.push(`${text.length} 字`);
    meta.textContent = parts.join(" · ");
    const wrap = row.querySelector(".bubble").parentElement;
    wrap.insertBefore(meta, wrap.querySelector(".message-tools"));
}

/* ------------------------------------------------------------------ */
/* 会话与历史                                                         */
/* ------------------------------------------------------------------ */
async function loadSessions() {
    try {
        const res = await fetch("/api/sessions");
        if (!res.ok) throw new Error("加载失败");
        const data = await res.json();
        renderSessions(data.sessions || []);
    } catch (_) {
        els.historyList.innerHTML =
            '<div class="history-empty">无法加载历史记录</div>';
    }
}

function renderSessions(sessions) {
    els.historyList.innerHTML = "";

    if (!sessions.length) {
        els.historyList.innerHTML =
            '<div class="history-empty">还没有对话记录<br>发送第一条消息开始吧</div>';
        return;
    }

    sessions.forEach((item) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "history-item";
        if (item.session_id === state.sessionId) {
            btn.classList.add("active");
        }
        btn.dataset.session = item.session_id;

        const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        icon.setAttribute("class", "h-icon");
        icon.setAttribute("viewBox", "0 0 24 24");
        icon.innerHTML =
            '<path d="M5 5h14v11H9l-4 4V5Z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>';

        const title = document.createElement("span");
        title.className = "h-title";
        title.textContent = item.title || "新对话";

        const del = document.createElement("span");
        del.className = "h-del";
        del.title = "删除对话";
        del.innerHTML =
            '<svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V5h6v2M7 7l1 13h8l1-13" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>';

        del.addEventListener("click", async (event) => {
            event.stopPropagation();
            await deleteSession(item.session_id);
        });

        btn.appendChild(icon);
        btn.appendChild(title);
        btn.appendChild(del);
        btn.addEventListener("click", () => openSession(item.session_id, item.title));

        els.historyList.appendChild(btn);
    });
}

async function openSession(sessionId, title) {
    if (state.generating) stopGenerating();
    state.sessionId = sessionId;
    localStorage.setItem("xz_session", sessionId);
    state.messages = [];
    state.title = title || "新对话";

    els.chatBox.querySelectorAll(".message-row").forEach((row) => row.remove());
    els.chatTitle.textContent = state.title;

    try {
        const res = await fetch(
            `/api/history?session_id=${encodeURIComponent(sessionId)}&limit=100`
        );
        const data = await res.json();
        const messages = data.messages || [];
        if (!messages.length) {
            showWelcome();
        } else {
            messages.forEach((item) => {
                if (item.role === "user" || item.role === "assistant") {
                    createRow(item.role, item.content);
                    state.messages.push({ role: item.role, content: item.content });
                }
            });
        }
    } catch (_) {
        toast("加载会话失败");
    }

    markActiveSession();
    closeSidebar();
    scrollToBottom(true);
}

async function deleteSession(sessionId) {
    if (!confirm("确定删除这条对话吗？")) return;
    try {
        await fetch(`/api/session?session_id=${encodeURIComponent(sessionId)}`, {
            method: "DELETE",
        });
        if (sessionId === state.sessionId) {
            startNewChat(false);
        }
        loadSessions();
        toast("对话已删除");
    } catch (_) {
        toast("删除失败");
    }
}

function markActiveSession() {
    els.historyList.querySelectorAll(".history-item").forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.session === state.sessionId);
    });
}

function startNewChat(close = true) {
    if (state.generating) stopGenerating();

    state.sessionId = newSessionId();
    localStorage.setItem("xz_session", state.sessionId);
    state.messages = [];
    state.title = "新对话";

    els.chatBox.querySelectorAll(".message-row").forEach((row) => row.remove());
    els.chatTitle.textContent = "新对话";
    showWelcome();
    els.chatBox.scrollTop = 0;

    markActiveSession();
    els.message.value = "";
    autoResize();
    updateSendState();
    if (close) {
        closeSidebar();
        els.message.focus();
    }
}

/* ------------------------------------------------------------------ */
/* 状态与模型                                                         */
/* ------------------------------------------------------------------ */
function setStatus(kind, text) {
    els.statusDot.className = `dot ${kind}`;
    els.statusText.textContent = text;
}

async function refreshStatus() {
    setStatus("loading", "正在连接云端模型…");
    let data;
    try {
        const res = await fetch("/api/status", { cache: "no-store" });
        data = await res.json();
    } catch (error) {
        setStatus("err", "无法连接小智服务");
        showBanner("无法连接小智服务，请稍后刷新页面重试。");
        return;
    }

    const providers = data.providers || [];
    const online = providers.find((item) => item.kind === "openai" && item.configured);
    if (!online) {
        setStatus("err", "在线模型未配置");
        showBanner("服务端尚未配置在线大模型 API Key，请联系管理员或检查环境变量。");
        els.modelSelect.innerHTML = '<option value="">在线模型未配置</option>';
        state.model = "";
        return;
    }

    state.defaultProvider = data.default_provider || online.id;
    hideBanner();
    setStatus("ok", `在线模型已连接 · ${online.name}`);

    try {
        const res = await fetch("/api/models", { cache: "no-store" });
        const payload = await res.json();
        populateModels(payload.models || [], payload.default_model || data.default_model);
    } catch (error) {
        populateModels([], data.default_model);
    }
}

function populateModels(models, defaultModel) {
    const names = models.map((item) => item.name || item.id).filter(Boolean);
    state.modelProviders = {};
    models.forEach((item) => {
        const key = item.name || item.id;
        if (key) state.modelProviders[key] = item.provider || "";
    });
    els.modelSelect.innerHTML = "";

    if (!names.length) {
        els.modelSelect.innerHTML = '<option value="">未配置模型</option>';
        state.model = "";
        showBanner("服务端尚未配置在线大模型，请联系管理员设置 API Key。");
        return;
    }

    names.forEach((name) => {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name;
        els.modelSelect.appendChild(option);
    });

    let selected = state.model;
    if (!selected || !names.includes(selected)) {
        selected = names.includes(defaultModel) ? defaultModel : names[0];
    }
    els.modelSelect.value = selected;
    state.model = selected;
    localStorage.setItem("xz_model", selected);
    updateThinkVisibility();
}

function updateThinkVisibility() {
    const isThinking = /qwen3|deepseek-r1|reasoner|thinking|qwq/i.test(state.model || "");
    state.modelThinking = isThinking;
    els.thinkWrap.classList.toggle("visible", isThinking);
}

function showBanner(text) {
    els.bannerText.textContent = text;
    els.banner.hidden = false;
}

function hideBanner() {
    els.banner.hidden = true;
}

/* ------------------------------------------------------------------ */
/* 发送与流式接收                                                     */
/* ------------------------------------------------------------------ */
function updateSendState() {
    if (state.generating) {
        els.sendBtn.disabled = false;
        els.sendBtn.classList.add("generating");
        els.sendBtn.title = "停止生成";
        return;
    }
    els.sendBtn.classList.remove("generating");
    els.sendBtn.title = "发送";
    els.sendBtn.disabled = els.message.value.trim().length === 0;
}

function autoResize() {
    els.message.style.height = "auto";
    els.message.style.height = Math.min(els.message.scrollHeight, 200) + "px";
}

function stopGenerating() {
    if (state.controller) {
        state.controller.abort();
    }
}

async function sendMessage(text) {
    const content = (text !== undefined ? text : els.message.value).trim();
    if (!content || state.generating) return;

    if (!state.model) {
        toast("云端模型尚未就绪，请检查服务配置");
        return;
    }

    hideWelcome();
    createRow("user", content);
    state.messages.push({ role: "user", content });
    state.lastUserText = content;

    if (state.messages.filter((m) => m.role === "user").length === 1) {
        state.title = content.replace(/\s+/g, " ").slice(0, 24);
        els.chatTitle.textContent = state.title;
    }

    if (text === undefined) {
        els.message.value = "";
        autoResize();
    }
    updateSendState();
    await requestAnswer();
}

async function requestAnswer(regenerate = false) {
    if (state.generating) return;

    state.generating = true;
    state.controller = new AbortController();
    updateSendState();

    const row = createAssistantPlaceholder();
    let answer = "";
    let thinking = "";
    let stats = null;
    let renderTimer = null;
    let dirty = false;

    const scheduleRender = (streaming) => {
        dirty = true;
        if (renderTimer) return;
        renderTimer = setTimeout(() => {
            renderTimer = null;
            if (!dirty) return;
            dirty = false;
            updateAssistantRow(row, answer, thinking, streaming);
            scrollToBottom();
        }, 55);
    };

    const payload = {
        provider: state.modelProviders[state.model] || "",
        model: state.model || null,
        session_id: state.sessionId,
        think: state.modelThinking ? state.think : null,
        regenerate,
        messages: state.messages.slice(-20),
    };

    try {
        const response = await fetch("/api/chat/stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            signal: state.controller.signal,
        });

        if (!response.ok) {
            let detail = `请求失败（HTTP ${response.status}）`;
            try {
                const data = await response.json();
                if (data.detail) detail = data.detail;
            } catch (_) { /* ignore */ }
            throw new Error(detail);
        }

        if (!response.body) throw new Error("当前浏览器不支持流式响应");

        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";
        let failed = null;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            let index;
            while ((index = buffer.indexOf("\n\n")) >= 0) {
                const raw = buffer.slice(0, index);
                buffer = buffer.slice(index + 2);

                for (const line of raw.split("\n")) {
                    if (!line.startsWith("data:")) continue;
                    const jsonText = line.slice(5).trim();
                    if (!jsonText) continue;
                    let event;
                    try {
                        event = JSON.parse(jsonText);
                    } catch (_) {
                        continue;
                    }
                    if (event.type === "delta") {
                        answer += event.content || "";
                        scheduleRender(true);
                    } else if (event.type === "thinking") {
                        thinking += event.content || "";
                        scheduleRender(true);
                    } else if (event.type === "done") {
                        stats = event.stats || null;
                    } else if (event.type === "error") {
                        failed = event.message || "生成失败";
                    }
                }
            }
        }

        if (renderTimer) {
            clearTimeout(renderTimer);
            renderTimer = null;
        }

        if (failed && !answer) {
            throw new Error(failed);
        }

        if (failed) {
            answer += `\n\n> ⚠️ 生成中断：${failed}`;
        }

        if (!answer.trim() && !thinking.trim()) {
            throw new Error("模型没有返回内容，请稍后重试或切换模型。");
        }

        row.classList.remove("waiting");
        updateAssistantRow(row, answer.trim(), thinking.trim(), false);
        setAssistantMeta(row, answer.trim(), stats);
        state.messages.push({ role: "assistant", content: answer.trim() });
        scrollToBottom();
        scheduleSessionsRefresh();
    } catch (error) {
        if (error.name === "AbortError") {
            if (answer.trim()) {
                row.classList.remove("waiting");
                updateAssistantRow(row, answer.trim(), thinking.trim(), false);
                fullNotice(row, "已停止生成");
                state.messages.push({ role: "assistant", content: answer.trim() });
            } else {
                row.remove();
            }
        } else {
            row.classList.remove("waiting");
            const bubble = row.querySelector(".bubble");
            bubble.innerHTML = "";
            const tip = document.createElement("div");
            tip.style.color = "var(--danger)";
            tip.textContent = "⚠️ " + error.message;
            bubble.appendChild(tip);
            const retry = document.createElement("button");
            retry.type = "button";
            retry.className = "tool-btn";
            retry.style.marginTop = "8px";
            retry.textContent = "重试";
            retry.addEventListener("click", () => {
                row.remove();
                const last = state.messages[state.messages.length - 1];
                if (last && last.role === "user") requestAnswer();
            });
            bubble.appendChild(retry);
        }
    } finally {
        state.generating = false;
        state.controller = null;
        updateSendState();
        els.message.focus();
    }
}

function fullNotice(row, message) {
    const meta = document.createElement("div");
    meta.className = "message-meta";
    meta.textContent = message;
    const wrap = row.querySelector(".bubble").parentElement;
    wrap.insertBefore(meta, wrap.querySelector(".message-tools"));
}

let sessionsTimer = null;
function scheduleSessionsRefresh() {
    clearTimeout(sessionsTimer);
    sessionsTimer = setTimeout(loadSessions, 700);
}

/* ------------------------------------------------------------------ */
/* 事件绑定                                                           */
/* ------------------------------------------------------------------ */
function bindEvents() {
    els.sendBtn.addEventListener("click", () => {
        if (state.generating) {
            stopGenerating();
        } else {
            sendMessage();
        }
    });

    els.message.addEventListener("input", () => {
        autoResize();
        updateSendState();
    });

    els.message.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            if (!state.generating) sendMessage();
        }
    });

    els.newChat.addEventListener("click", () => startNewChat());

    els.modelSelect.addEventListener("change", () => {
        state.model = els.modelSelect.value;
        localStorage.setItem("xz_model", state.model);
        updateThinkVisibility();
    });

    els.thinkToggle.checked = state.think;
    els.thinkToggle.addEventListener("change", () => {
        state.think = els.thinkToggle.checked;
        localStorage.setItem("xz_think", state.think ? "1" : "0");
    });

    els.themeBtn.addEventListener("click", toggleTheme);
    els.bannerBtn.addEventListener("click", refreshStatus);

    els.menuBtn.addEventListener("click", () => {
        els.app.classList.toggle("sidebar-open");
    });
    els.backdrop.addEventListener("click", closeSidebar);

    els.chatBox.addEventListener("click", async (event) => {
        const copyCode = event.target.closest(".code-copy");
        if (copyCode) {
            const code = copyCode.closest(".code-block").querySelector("code");
            if (code) await copyText(code.textContent);
            return;
        }

        const copyBtn = event.target.closest(".copy-btn");
        if (copyBtn) {
            const row = copyBtn.closest(".message-row");
            if (row) await copyText(row.dataset.raw || "");
            return;
        }

        const regenBtn = event.target.closest(".regen-btn");
        if (regenBtn) {
            if (state.generating) return;
            const row = regenBtn.closest(".message-row");
            const assistantRows = Array.from(els.chatBox.querySelectorAll(".message-row.assistant"));
            const isLastAssistant = assistantRows[assistantRows.length - 1] === row;
            const last = state.messages[state.messages.length - 1];
            if (isLastAssistant && last && last.role === "assistant") {
                state.messages.pop();
                row.remove();
                requestAnswer(true);
            } else {
                toast("只能重新生成最后一条回答");
            }
        }
    });

    // 欢迎页推荐问题
    const suggestions = $("suggestions");
    if (suggestions) {
        suggestions.addEventListener("click", (event) => {
            const button = event.target.closest("button[data-prompt]");
            if (button) sendMessage(button.dataset.prompt);
        });
    }

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            if (state.generating) stopGenerating();
            closeSidebar();
        }
    });
}

function closeSidebar() {
    els.app.classList.remove("sidebar-open");
}

/* ------------------------------------------------------------------ */
/* 主题                                                               */
/* ------------------------------------------------------------------ */
function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("xz_theme", theme);
}

function toggleTheme() {
    const current = document.documentElement.getAttribute("data-theme");
    applyTheme(current === "dark" ? "light" : "dark");
}

/* ------------------------------------------------------------------ */
/* 启动                                                               */
/* ------------------------------------------------------------------ */
async function init() {
    applyTheme(localStorage.getItem("xz_theme") || "light");
    // 首次进入也把会话号写入本地，刷新后仍能恢复对话
    localStorage.setItem("xz_session", state.sessionId);
    hideWelcome();
    els.chatTitle.textContent = "新对话";

    bindEvents();
    updateThinkVisibility();
    updateSendState();
    await refreshStatus();

    // 恢复上次的会话
    const saved = localStorage.getItem("xz_session");
    if (saved) {
        state.sessionId = saved;
        try {
            const res = await fetch(
                `/api/history?session_id=${encodeURIComponent(saved)}&limit=100`
            );
            const data = await res.json();
            const messages = data.messages || [];
            if (messages.length) {
                state.messages = [];
                messages.forEach((item) => {
                    if (item.role === "user" || item.role === "assistant") {
                        createRow(item.role, item.content);
                        state.messages.push({ role: item.role, content: item.content });
                    }
                });
                const firstUser = messages.find((m) => m.role === "user");
                if (firstUser) {
                    state.title = firstUser.content.replace(/\s+/g, " ").slice(0, 24);
                    els.chatTitle.textContent = state.title;
                }
            } else {
                showWelcome();
            }
        } catch (_) {
            showWelcome();
        }
    } else {
        showWelcome();
    }

    loadSessions();
    els.message.focus();

    // 定期检查本地模型状态（仅在页面可见且空闲时）
    setInterval(() => {
        if (!state.generating && document.visibilityState === "visible") {
            refreshStatus();
        }
    }, 60000);
}

init();

/* ============================================================
   小智萌宠 & 跳台阶小游戏
   ------------------------------------------------------------
   · 打开网页就站在输入框上方
   · 单击：随机可爱动作 + 气泡台词
   · 长按拖动：可以把萌宠放到屏幕任意位置（位置会被记住）
   · 连点三下：进入「小智跳台阶」小游戏
   ============================================================ */

function createJumpGame() {
    const layer = document.getElementById("game-layer");
    const canvas = document.getElementById("game-canvas");
    if (!layer || !canvas) return null;

    const ctx = canvas.getContext("2d");
    const scoreEl = document.getElementById("game-score");
    const bestEl = document.getElementById("game-best");
    const panel = document.getElementById("game-panel");
    const titleEl = document.getElementById("game-panel-title");
    const textEl = document.getElementById("game-panel-text");
    const panelScore = document.getElementById("game-panel-score");
    const startBtn = document.getElementById("game-start");
    const backBtn = document.getElementById("game-back");
    const exitBtn = document.getElementById("game-exit");
    const petImg = document.getElementById("mascot-img");

    const GRAVITY = 2200;
    const JUMP = 840;
    const MAX_VX = 360;
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

    let W = 0;
    let H = 0;
    let dpr = 1;
    let raf = 0;
    let running = false;
    let last = 0;
    let pet = { x: 0, y: 0, vx: 0, vy: 0 };
    let petW = 62;
    let petH = 76;
    let platforms = [];
    let distance = 0;
    let score = 0;
    let best = Number(localStorage.getItem("xz_game_best") || 0) || 0;
    let stars = [];
    let pointerX = null;
    const keys = { left: false, right: false };

    function resize() {
        dpr = Math.min(2, window.devicePixelRatio || 1);
        W = layer.clientWidth || window.innerWidth;
        H = layer.clientHeight || window.innerHeight;
        canvas.width = Math.round(W * dpr);
        canvas.height = Math.round(H * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function roundRect(x, y, w, h, r) {
        const rr = Math.min(r, h / 2, w / 2);
        ctx.beginPath();
        ctx.moveTo(x + rr, y);
        ctx.arcTo(x + w, y, x + w, y + h, rr);
        ctx.arcTo(x + w, y + h, x, y + h, rr);
        ctx.arcTo(x, y + h, x, y, rr);
        ctx.arcTo(x, y, x + w, y, rr);
        ctx.closePath();
    }

    function reset() {
        resize();
        petH = clamp(H * 0.115, 66, 92);
        petW = petH * (343 / 480);
        pet.x = W / 2;
        pet.y = H - 150;
        pet.vx = 0;
        pet.vy = 0;

        distance = 0;
        score = 0;
        pointerX = null;
        keys.left = false;
        keys.right = false;

        platforms = [];
        const baseW = clamp(W * 0.26, 130, 200);
        platforms.push({ x: clamp(W / 2 - baseW / 2, 12, W - baseW - 12), y: H - 96, w: baseW, h: 14 });
        ensurePlatforms();

        stars = Array.from({ length: 54 }, () => ({
            x: Math.random() * W,
            y: Math.random() * H,
            r: Math.random() * 1.5 + 0.4,
            a: Math.random() * 0.5 + 0.2,
        }));
        updateHud();
    }

    function ensurePlatforms() {
        let topY = platforms.reduce((m, p) => Math.min(m, p.y), Infinity);
        while (platforms.length < 44 && topY > -160) {
            const w = Math.max(88, 150 - distance / 320 - Math.random() * 30);
            const gap = 96 + Math.random() * 38 + Math.min(10, distance / 1200);
            topY -= gap;
            platforms.push({ x: 12 + Math.random() * Math.max(1, W - w - 24), y: topY, w, h: 14 });
        }
    }

    function updateHud() {
        scoreEl.textContent = String(score);
        bestEl.textContent = String(best);
    }

    function update(dt) {
        let target = 0;
        if (keys.left) target -= MAX_VX;
        if (keys.right) target += MAX_VX;
        if (pointerX !== null) {
            target = clamp((pointerX - pet.x) * 6, -MAX_VX, MAX_VX);
        }
        pet.vx += (target - pet.vx) * Math.min(1, dt * 14);
        pet.x += pet.vx * dt;
        if (pet.x < petW / 2) {
            pet.x = petW / 2;
            pet.vx = 0;
        } else if (pet.x > W - petW / 2) {
            pet.x = W - petW / 2;
            pet.vx = 0;
        }

        pet.vy += GRAVITY * dt;
        const prevBottom = pet.y + petH / 2;
        pet.y += pet.vy * dt;
        const bottom = pet.y + petH / 2;

        if (pet.vy > 0) {
            for (const p of platforms) {
                if (bottom >= p.y && prevBottom <= p.y + 10 &&
                    pet.x + petW * 0.3 > p.x && pet.x - petW * 0.3 < p.x + p.w) {
                    pet.y = p.y - petH / 2;
                    pet.vy = -JUMP;
                    break;
                }
            }
        }

        const line = H * 0.42;
        if (pet.y < line) {
            const shift = line - pet.y;
            pet.y = line;
            for (const p of platforms) p.y += shift;
            distance += shift;
            score = Math.floor(distance / 10);
        }

        platforms = platforms.filter((p) => p.y < H + 90);
        ensurePlatforms();

        if (pet.y - petH / 2 > H + 20) {
            gameOver();
            return;
        }
        updateHud();
    }

    function draw() {
        const sky = ctx.createLinearGradient(0, 0, 0, H);
        sky.addColorStop(0, "#091229");
        sky.addColorStop(0.55, "#0f2047");
        sky.addColorStop(1, "#182a63");
        ctx.fillStyle = sky;
        ctx.fillRect(0, 0, W, H);

        for (const s of stars) {
            ctx.globalAlpha = s.a;
            ctx.fillStyle = "#cfe0ff";
            ctx.beginPath();
            ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
            ctx.fill();
        }
        ctx.globalAlpha = 1;

        const mx = W * 0.82;
        const my = H * 0.15;
        const glow = ctx.createRadialGradient(mx, my, 4, mx, my, 100);
        glow.addColorStop(0, "rgba(191, 219, 254, 0.5)");
        glow.addColorStop(1, "rgba(191, 219, 254, 0)");
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(mx, my, 100, 0, Math.PI * 2);
        ctx.fill();

        for (const p of platforms) {
            if (p.y < -40 || p.y > H + 40) continue;
            ctx.fillStyle = "rgba(2, 6, 23, 0.4)";
            roundRect(p.x + 2, p.y + 5, p.w, p.h, 8);
            ctx.fill();

            const pg = ctx.createLinearGradient(0, p.y, 0, p.y + p.h);
            pg.addColorStop(0, "#7dd3fc");
            pg.addColorStop(0.4, "#38bdf8");
            pg.addColorStop(1, "#0369a1");
            ctx.fillStyle = pg;
            roundRect(p.x, p.y, p.w, p.h, 8);
            ctx.fill();

            ctx.fillStyle = "rgba(224, 242, 254, 0.6)";
            roundRect(p.x + 3, p.y + 2, p.w - 6, 3.5, 2);
            ctx.fill();
        }

        ctx.save();
        ctx.translate(pet.x, pet.y);
        ctx.rotate(clamp(pet.vx / 1500, -0.22, 0.22));
        if (petImg && petImg.complete && petImg.naturalWidth) {
            ctx.drawImage(petImg, -petW / 2, -petH / 2, petW, petH);
        } else {
            ctx.fillStyle = "#7dd3fc";
            ctx.beginPath();
            ctx.arc(0, 0, petW / 2, 0, Math.PI * 2);
            ctx.fill();
        }
        ctx.restore();
    }

    function frame(t) {
        if (!running) return;
        if (!last) last = t;
        let dt = (t - last) / 1000;
        last = t;
        dt = Math.min(dt, 0.033);
        update(dt);
        if (!running) return;
        draw();
        raf = requestAnimationFrame(frame);
    }

    function gameOver() {
        running = false;
        cancelAnimationFrame(raf);
        if (score > best) {
            best = score;
            try { localStorage.setItem("xz_game_best", String(best)); } catch (_) {}
        }
        updateHud();
        panel.hidden = false;
        titleEl.textContent = "哎呀，掉下去啦";
        textEl.innerHTML = "别灰心，小智还能跳得更高！";
        panelScore.hidden = false;
        panelScore.innerHTML = `本局得分 <b>${score}</b> · 最佳 <b>${best}</b>`;
        startBtn.textContent = "再来一局";
    }

    function start() {
        layer.hidden = false;
        document.body.classList.add("game-open");
        reset();
        panel.hidden = true;
        running = true;
        last = 0;
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(frame);
    }

    function open() {
        layer.hidden = false;
        document.body.classList.add("game-open");
        resize();
        reset();
        draw();
        bestEl.textContent = String(best);
        scoreEl.textContent = "0";
        panel.hidden = false;
        panelScore.hidden = true;
        titleEl.textContent = "小智跳台阶";
        textEl.innerHTML = "左右移动，踩着台阶一直往上跳。<br>掉下去就结束啦！";
        startBtn.textContent = "开始游戏";
        running = false;
        cancelAnimationFrame(raf);
    }

    function close() {
        running = false;
        cancelAnimationFrame(raf);
        layer.hidden = true;
        document.body.classList.remove("game-open");
        pointerX = null;
        keys.left = false;
        keys.right = false;
    }

    canvas.addEventListener("pointerdown", (e) => {
        if (!running) return;
        pointerX = e.clientX - canvas.getBoundingClientRect().left;
        try { canvas.setPointerCapture(e.pointerId); } catch (_) {}
    });
    canvas.addEventListener("pointermove", (e) => {
        if (!running) return;
        pointerX = e.clientX - canvas.getBoundingClientRect().left;
    });
    canvas.addEventListener("pointerup", () => { pointerX = null; });
    canvas.addEventListener("pointercancel", () => { pointerX = null; });
    canvas.addEventListener("pointerleave", (e) => {
        if (e.pointerType === "mouse") pointerX = null;
    });

    window.addEventListener("keydown", (e) => {
        if (!running) return;
        if (e.key === "ArrowLeft" || e.key === "a" || e.key === "A") {
            keys.left = true;
            e.preventDefault();
        } else if (e.key === "ArrowRight" || e.key === "d" || e.key === "D") {
            keys.right = true;
            e.preventDefault();
        }
    });
    window.addEventListener("keyup", (e) => {
        if (e.key === "ArrowLeft" || e.key === "a" || e.key === "A") keys.left = false;
        if (e.key === "ArrowRight" || e.key === "d" || e.key === "D") keys.right = false;
    });

    window.addEventListener("resize", () => {
        if (!layer.hidden) {
            resize();
            if (running) {
                pet.x = clamp(pet.x, petW / 2, W - petW / 2);
                pet.y = clamp(pet.y, -200, H);
            } else {
                reset();
                draw();
            }
        }
    });

    startBtn.addEventListener("click", start);
    backBtn.addEventListener("click", close);
    exitBtn.addEventListener("click", close);

    return { open, close, start };
}

function initMascot() {
    const wrap = document.getElementById("mascot-wrap");
    const img = document.getElementById("mascot-img");
    const bubble = document.getElementById("mascot-bubble");
    if (!wrap || !img) return;

    const STORE_POS = "xz_mascot_pos";
    const STORE_SEEN = "xz_mascot_seen";
    // 隐私模式下 localStorage 可能直接抛异常，读写都单独兜底，避免萌宠整体失效。
    const readStore = (key) => {
        try { return localStorage.getItem(key); } catch (_) { return null; }
    };
    const writeStore = (key, value) => {
        try { localStorage.setItem(key, value); } catch (_) {}
    };
    const composer = document.querySelector(".composer");
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

    const game = createJumpGame();

    let custom = null;
    let pressTimer = 0;
    let dragging = false;
    let pressCancelled = false;
    let startX = 0;
    let startY = 0;
    let startLeft = 0;
    let startTop = 0;
    let pid = null;
    let clickCount = 0;
    let clickTimer = 0;
    let bubbleTimer = 0;
    let bubbleFadeTimer = 0;
    let lastAction = -1;
    let actionLock = false;
    let pendingAction = false;
    let actionSeq = 0;
    let actionTimer = 0;
    let nextActionTimer = 0;

    const ACTIONS = [
        { cls: "act-hop", lines: ["嘿咻！", "看我跳得高不高～"] },
        { cls: "act-shake", lines: ["嘿嘿～", "别挠我痒痒！"] },
        { cls: "act-twirl", lines: ["转个身～", "看我转得圆不圆？"] },
        { cls: "act-squish", lines: ["咕叽～", "我软软的哦"] },
        { cls: "act-wave", lines: ["在呢在呢！", "有什么想问我的？"] },
    ];

    function size() {
        return { w: wrap.offsetWidth || 88, h: wrap.offsetHeight || 108 };
    }

    function defaultPos() {
        const s = size();
        const card = document.querySelector(".composer-inner") || composer;
        const rect = card
            ? card.getBoundingClientRect()
            : { left: window.innerWidth / 2 - 200, top: window.innerHeight - 140, width: 400 };
        // Sit on the top-right corner of the input card, peeking over the edge.
        const x = rect.left + rect.width - s.w + 14;
        const y = rect.top - s.h + 12;
        return {
            x: clamp(x, 8, window.innerWidth - s.w - 8),
            y: clamp(y, 8, window.innerHeight - s.h - 8),
        };
    }

    function applyPos(x, y) {
        const s = size();
        const nx = clamp(x, 6, window.innerWidth - s.w - 6);
        const ny = clamp(y, 6, window.innerHeight - s.h - 6);
        wrap.style.left = nx + "px";
        wrap.style.top = ny + "px";
        return { x: nx, y: ny };
    }

    function savePos(pos) {
        writeStore(STORE_POS, JSON.stringify(pos));
    }

    function markSeen() {
        writeStore(STORE_SEEN, "1");
    }

    function positionBubble() {
        bubble.classList.remove("bubble-left", "bubble-below");
        const r = wrap.getBoundingClientRect();
        // 默认贴在萌宠右上方；靠近屏幕左/上边缘时自动换边，避免气泡被裁掉。
        if (r.left < 236) bubble.classList.add("bubble-left");
        if (r.top < 104) bubble.classList.add("bubble-below");
    }

    function hideBubble(immediate) {
        clearTimeout(bubbleTimer);
        bubble.classList.remove("show");
        clearTimeout(bubbleFadeTimer);
        if (immediate) {
            bubble.hidden = true;
        } else {
            bubbleFadeTimer = setTimeout(() => { bubble.hidden = true; }, 220);
        }
    }

    function showBubble(text, duration) {
        const visible = !bubble.hidden;
        bubble.textContent = text;
        bubble.hidden = false;
        positionBubble();
        clearTimeout(bubbleTimer);
        clearTimeout(bubbleFadeTimer);
        if (visible) {
            bubble.classList.add("show");
        } else {
            bubble.classList.remove("show");
            requestAnimationFrame(() => requestAnimationFrame(() => bubble.classList.add("show")));
        }
        bubbleTimer = setTimeout(() => hideBubble(false), duration || 1900);
    }

    function sparkle() {
        if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
        for (let k = 0; k < 3; k += 1) {
            const s = document.createElement("span");
            s.className = "mascot-spark";
            s.style.left = (46 + Math.random() * 18) + "%";
            s.style.top = (22 + Math.random() * 16) + "%";
            s.style.setProperty("--dx", (Math.random() * 44 - 22).toFixed(0) + "px");
            s.style.setProperty("--dy", (-(20 + Math.random() * 24)).toFixed(0) + "px");
            s.style.animationDelay = (k * 45) + "ms";
            s.addEventListener("animationend", () => s.remove(), { once: true });
            wrap.appendChild(s);
        }
    }

    function pickAction() {
        let i = Math.floor(Math.random() * ACTIONS.length);
        if (ACTIONS.length > 1 && i === lastAction) i = (i + 1) % ACTIONS.length;
        return i;
    }

    function finishAction() {
        if (!actionLock) return;
        actionLock = false;
        actionSeq += 1;
        clearTimeout(actionTimer);
        ACTIONS.forEach((a) => img.classList.remove(a.cls));
        wrap.classList.remove("acting");
        if (pendingAction) {
            pendingAction = false;
            nextActionTimer = setTimeout(playAction, 150);
        }
    }

    function interruptAction() {
        actionSeq += 1;
        actionLock = false;
        pendingAction = false;
        clearTimeout(actionTimer);
        clearTimeout(nextActionTimer);
        ACTIONS.forEach((a) => img.classList.remove(a.cls));
        wrap.classList.remove("acting");
    }

    function startAction(i) {
        const action = ACTIONS[i];
        const seq = ++actionSeq;
        actionLock = true;
        lastAction = i;
        ACTIONS.forEach((a) => img.classList.remove(a.cls));
        void img.offsetWidth;
        img.classList.add(action.cls);
        wrap.classList.add("acting");
        clearTimeout(actionTimer);
        actionTimer = setTimeout(finishAction, 1150);
        // 动作起势后再冒台词，动作与气泡一前一后，看着更自然。
        setTimeout(() => {
            if (seq === actionSeq && actionLock) {
                showBubble(action.lines[Math.floor(Math.random() * action.lines.length)]);
            }
        }, 130);
    }

    function playAction() {
        if (actionLock) {
            pendingAction = true;
            return;
        }
        startAction(pickAction());
    }

    img.addEventListener("animationend", (e) => {
        if (!actionLock) return;
        if (!e.animationName || !e.animationName.startsWith("mascot-")) return;
        if (e.animationName === "mascot-idle") return;
        finishAction();
    });

    function openGame() {
        interruptAction();
        hideBubble(true);
        if (game) game.open();
    }

    function handleTap() {
        markSeen();
        clickCount += 1;
        clearTimeout(clickTimer);
        clickTimer = setTimeout(() => { clickCount = 0; }, 700);
        sparkle();
        if (clickCount >= 3) {
            clickCount = 0;
            clearTimeout(clickTimer);
            interruptAction();
            hideBubble(true);
            showBubble("游戏时间！", 460);
            setTimeout(() => {
                hideBubble(true);
                if (game) game.open();
            }, 400);
            return;
        }
        playAction();
    }

    try {
        const saved = JSON.parse(readStore(STORE_POS) || "null");
        if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) custom = saved;
    } catch (_) {}

    wrap.hidden = false;
    applyPos(custom ? custom.x : defaultPos().x, custom ? custom.y : defaultPos().y);

    const reveal = () => requestAnimationFrame(() => wrap.classList.add("ready"));
    if (img.complete) reveal();
    else {
        img.addEventListener("load", reveal, { once: true });
        setTimeout(reveal, 900);
    }

    const syncPos = () => {
        const target = custom || defaultPos();
        applyPos(target.x, target.y);
    };
    const syncLayout = () => {
        syncPos();
        if (!bubble.hidden) positionBubble();
    };

    window.addEventListener("resize", syncLayout);

    // 首屏的历史列表/欢迎页会把输入框位置挤动，等布局稳定后再贴一次，
    // 避免萌宠停在半空而没站在输入卡片上沿。
    requestAnimationFrame(() => requestAnimationFrame(syncLayout));
    window.addEventListener("load", syncLayout);
    // 欢迎页/历史列表/字体会在首屏陆续撑开，分几个时间点再贴一次输入框上沿。
    [120, 400, 900, 1600].forEach((ms) => setTimeout(syncLayout, ms));
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(syncLayout).catch(() => {});
    if (window.ResizeObserver && composer) {
        try { new ResizeObserver(syncLayout).observe(composer); } catch (_) {}
    }

    // 手机弹出软键盘时，先收起萌宠；键盘收回后再让它回来。
    if (window.visualViewport) {
        const vv = window.visualViewport;
        let keyboardUp = false;
        const syncKeyboard = () => {
            const shrunk = window.innerHeight - vv.height > 140;
            if (shrunk === keyboardUp) return;
            keyboardUp = shrunk;
            wrap.classList.toggle("keyboard-up", keyboardUp);
            if (keyboardUp) hideBubble(false);
        };
        vv.addEventListener("resize", syncKeyboard);
        vv.addEventListener("scroll", syncKeyboard);
    }

    wrap.addEventListener("contextmenu", (e) => e.preventDefault());

    wrap.tabIndex = 0;
    wrap.setAttribute("role", "button");
    wrap.setAttribute("aria-label", "小智萌宠：点一下互动，长按拖动我，连点三下进入小游戏");
    wrap.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            handleTap();
        }
    });

    function beginDrag() {
        dragging = true;
        wrap.classList.add("dragging");
        interruptAction();
        hideBubble(false);
    }

    wrap.addEventListener("pointerdown", (e) => {
        if (e.pointerType === "mouse" && e.button !== 0) return;
        pid = e.pointerId;
        startX = e.clientX;
        startY = e.clientY;
        const r = wrap.getBoundingClientRect();
        startLeft = r.left;
        startTop = r.top;
        pressCancelled = false;
        try { wrap.setPointerCapture(pid); } catch (_) {}
        pressTimer = setTimeout(() => {
            if (dragging) return;
            beginDrag();
            if (navigator.vibrate) navigator.vibrate(12);
        }, 300);
    });

    wrap.addEventListener("pointermove", (e) => {
        if (pid === null || e.pointerId !== pid) return;
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        if (!dragging) {
            // 鼠标移动 9px 即拖动；触摸端提高到 16px，避免轻点时手指抖动被误判为拖拽。
            const threshold = e.pointerType === "mouse" ? 9 : 16;
            if (Math.hypot(dx, dy) > threshold) {
                pressCancelled = true;
                clearTimeout(pressTimer);
                beginDrag();
            } else {
                return;
            }
        }
        e.preventDefault();
        applyPos(startLeft + dx, startTop + dy);
    });

    function endPointer(e, cancelled) {
        if (pid === null) return;
        if (e && e.pointerId !== pid) return;
        clearTimeout(pressTimer);
        const wasDragging = dragging;
        pid = null;
        dragging = false;
        wrap.classList.remove("dragging");
        try { wrap.releasePointerCapture(e.pointerId); } catch (_) {}
        if (wasDragging) {
            const r = wrap.getBoundingClientRect();
            custom = { x: r.left, y: r.top };
            savePos(custom);
        } else if (!cancelled && !pressCancelled) {
            handleTap();
        }
    }

    wrap.addEventListener("pointerup", (e) => endPointer(e, false));
    wrap.addEventListener("pointercancel", (e) => endPointer(e, true));

    if (location.hash === "#game" || location.hash === "#play") {
        setTimeout(() => {
            openGame();
            if (location.hash === "#play" && game) setTimeout(() => game.start(), 260);
        }, 500);
    }

    if (!readStore(STORE_SEEN)) {
        setTimeout(() => {
            // 用户已经点过萌宠就不再补欢迎气泡，避免覆盖掉互动台词。
            if (!document.hidden && !readStore(STORE_SEEN)) {
                showBubble("点我一下！长按可以拖动我，连点三下有小游戏～", 5200);
            }
        }, 1600);
    }
}

// 即使初始化出现意外，也让萌宠按 CSS 兜底位置显示，而不是整块消失。
try {
    initMascot();
} catch (err) {
    const wrap = document.getElementById("mascot-wrap");
    if (wrap) {
        wrap.hidden = false;
        wrap.classList.add("ready");
    }
    console.error("[小智] 萌宠初始化失败：", err);
}
