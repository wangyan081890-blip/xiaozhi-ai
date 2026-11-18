// 临时 UI 冒烟测试：连接 8200，检查在线状态、发送一条消息并读取回答。
import http from "node:http";

const BASE = process.env.BASE || "http://127.0.0.1:8200/";
const PORT = Number(process.env.CDP_PORT || 9224);

function getJson(path) {
  return new Promise((resolve, reject) => {
    http.get({ host: "127.0.0.1", port: PORT, path }, (res) => {
      let body = "";
      res.on("data", (c) => (body += c));
      res.on("end", () => {
        try { resolve(JSON.parse(body)); } catch (e) { reject(e); }
      });
    }).on("error", reject);
  });
}

class CDP {
  constructor(url) { this.ws = new WebSocket(url); this.id = 0; this.pending = new Map(); this.errors = []; }
  ready() {
    return new Promise((res, rej) => {
      this.ws.onopen = () => res();
      this.ws.onerror = (e) => rej(e);
      this.ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.method === "Runtime.exceptionThrown") {
          this.errors.push(msg.params.exceptionDetails.text || "exception");
        }
        if (msg.id && this.pending.has(msg.id)) {
          const { resolve, reject } = this.pending.get(msg.id);
          this.pending.delete(msg.id);
          msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
        }
      };
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
  async eval(expression) {
    const r = await this.send("Runtime.evaluate", {
      expression, awaitPromise: true, returnByValue: true,
    });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
    return r.result.value;
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const pages = await getJson("/json/list");
const page = pages.find((p) => p.type === "page");
const cdp = new CDP(page.webSocketDebuggerUrl);
await cdp.ready();

await cdp.send("Page.enable");
await cdp.send("Runtime.enable");
await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
await cdp.send("Page.navigate", { url: BASE });
await sleep(2500);

console.log("STATUS", await cdp.eval(`document.querySelector('#status-text').textContent`));
console.log("MODELS", await cdp.eval(`[...document.querySelectorAll('#model-select option')].map(o => o.textContent)`));

await cdp.eval(`(() => {
  const box = document.querySelector('#message');
  box.value = '你好，做个自我介绍';
  box.dispatchEvent(new Event('input', { bubbles: true }));
  document.querySelector('#send-btn').click();
})()`);

let answer = "";
for (let i = 0; i < 40; i++) {
  await sleep(400);
  answer = await cdp.eval(`(() => {
    const rows = [...document.querySelectorAll('.message-row.assistant')];
    const last = rows[rows.length - 1];
    if (!last) return '';
    const bubble = last.querySelector('.bubble');
    const meta = last.querySelector('.message-meta');
    return bubble ? (meta ? meta.textContent + ' || ' : '') + bubble.innerText : '';
  })()`);
  const busy = await cdp.eval(`!!document.querySelector('.bubble.streaming') || document.querySelector('#send-btn').classList.contains('generating')`);
  if (answer && !busy) break;
}
console.log("ANSWER", answer.slice(0, 220));
console.log("EXCEPTIONS", JSON.stringify(cdp.errors));
cdp.ws.close();
process.exit(0);
