// 工作台渲染核对：真起一个浏览器打开工作台，等「采集抓拍」卡片出现，
// 并确认卡片里的图**真的解码成功**（naturalWidth > 0），而不是一个裂图。
//
//   node modules/integration/check_workbench_render.mjs [http://127.0.0.1:8000/admin]
//
// 浏览器按顺序找：环境变量 CHROME_EXE → Edge → Playwright 缓存的 chromium。
// 只依赖 Node 内置的 fetch / WebSocket（Node 22+），不装任何包。
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const PORT = 9333;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const home = process.env.USERPROFILE || process.env.HOME || "";

function findBrowser() {
  const candidates = [
    process.env.CHROME_EXE,
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium",
    home && join(home, "AppData/Local/ms-playwright/chromium_headless_shell-1148/chrome-win/headless_shell.exe"),
    home && join(home, "AppData/Local/ms-playwright/chromium-1148/chrome-win/chrome.exe"),
  ].filter(Boolean);
  return candidates.find((p) => existsSync(p)) || null;
}

// 卡片里的每一项都取出来单独断言，失败时说得出是哪一项没出来
const PROBE = `JSON.stringify({
  hasCard: !!document.querySelector('.capture-panel'),
  heading: document.querySelector('.capture-panel .panel-heading h2')?.textContent || null,
  dish: document.querySelector('.capture-panel .capture-read h3')?.textContent || null,
  verdict: document.querySelector('.capture-panel .capture-verdict')?.innerText || null,
  binding: document.querySelector('.capture-panel .capture-binding')?.innerText || null,
  note: document.querySelector('.capture-panel .capture-note')?.textContent || null,
  shot: (() => {
    const i = document.querySelector('.capture-panel .capture-shot img');
    if (!i) return { present: false };
    return { present: true, src: i.getAttribute('src'), complete: i.complete,
             naturalWidth: i.naturalWidth, naturalHeight: i.naturalHeight };
  })(),
  strip: document.querySelectorAll('.capture-panel .capture-strip li').length,
  loading: !!document.querySelector('.loading-surface')
})`;

async function main() {
  const url = process.argv[2] || "http://127.0.0.1:8000/admin";
  const browser = findBrowser();
  if (!browser) {
    console.error("找不到浏览器；用 CHROME_EXE 指定一个 Chromium 系可执行文件");
    return 2;
  }

  const child = spawn(browser, [
    `--remote-debugging-port=${PORT}`, "--disable-gpu", "--no-sandbox",
    "--no-first-run", `--user-data-dir=${join(tmpdir(), "workbench-render-check")}`,
    "about:blank",
  ], { stdio: "ignore" });

  let ws, nextId = 1;
  const pending = new Map();
  const send = (method, params = {}) => {
    const id = nextId++;
    ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
  };

  try {
    let page = null;
    for (let i = 0; i < 40 && !page; i++) {
      try {
        const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
        page = list.find((t) => t.type === "page" && t.webSocketDebuggerUrl);
      } catch { /* 调试端口还没起来，继续等 */ }
      if (!page) await sleep(250);
    }
    if (!page) throw new Error(`CDP 端口 ${PORT} 没起来`);

    ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) { pending.get(m.id).resolve(m); pending.delete(m.id); }
    };
    await send("Page.enable");
    await send("Page.navigate", { url });

    let seen = null;
    for (let i = 0; i < 60; i++) {
      await sleep(500);
      const r = await send("Runtime.evaluate", { expression: PROBE, returnByValue: true });
      const raw = r.result?.result?.value;
      if (!raw) continue;
      seen = JSON.parse(raw);
      if (seen.hasCard && seen.shot.naturalWidth > 0) break;
    }

    console.log(JSON.stringify({ url, browser, ...seen }, null, 2));
    const failures = [];
    if (!seen?.hasCard) failures.push("卡片 .capture-panel 没渲染出来");
    else {
      if (!seen.shot.present) failures.push("卡片在，但里面没有 <img>");
      else if (!(seen.shot.naturalWidth > 0)) failures.push(`图片没解码：${seen.shot.src}`);
      if (!seen.binding) failures.push("没有视觉校验那一行");
      if (!seen.dish) failures.push("没有菜品名");
      if (seen.strip < 1) failures.push("缩略图带是空的");
    }
    if (failures.length) {
      console.error(`\n核对不通过（工作台 ${url}）：\n  - ` + failures.join("\n  - "));
      return 1;
    }
    console.log(`\n核对通过：卡片渲染出来了，图片 ${seen.shot.naturalWidth}×${seen.shot.naturalHeight} 真的解码成功`);
    return 0;
  } finally {
    try { ws?.close(); } catch { /* 连接可能已经断了 */ }
    child.kill();
  }
}

process.exit(await main());
