// 工作台渲染核对：真起一个浏览器打开工作台，等目标卡片出现，再读 DOM 断言。
//
//   node modules/integration/check_workbench_render.mjs                 # 抓拍卡片
//   node modules/integration/check_workbench_render.mjs --check samples # 样例数据接入
//
// 断言读的是 DOM 里真实渲染出来的内容，尤其是图片的 naturalWidth——
// 只有图片**真的解码成功**才大于 0。光比对 <img src> 是看不出裂图的，
// 而裂图（路径错、存储取不到、MIME 不对）正是这条链路最容易出的问题。
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

// 像真人那样点：Radix 的 Tabs / Menu 只在 mousedown 或 focus 上切换，
// 光派发 click() 不会生效（会得到一个「看起来点了但页签没动」的假结果）。
const ACTIVATE = `(el) => {
  if (!el) return false;
  for (const type of ['pointerdown','mousedown','pointerup','mouseup','click'])
    el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true,
      button: 0, buttons: 1, pointerType: 'mouse', isPrimary: true }));
  return true;
}`;

// 切到「门店设置 → 数据接入」页签
const OPEN_SETTINGS = `(() => { const a=${ACTIVATE};
  return a([...document.querySelectorAll('.nav-item')].find(x=>x.textContent.includes('门店设置'))); })()`;
const OPEN_CONNECTIONS = `(() => { const a=${ACTIVATE};
  return a([...document.querySelectorAll('[data-slot=tabs-trigger]')].find(x=>x.textContent.includes('数据接入'))); })()`;

const CHECKS = {
  capture: {
    title: "工作台抓拍卡片",
    url: "http://127.0.0.1:8000/admin",
    steps: [],
    probe: `JSON.stringify({
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
    })`,
    ready: (s) => s.hasCard && s.shot.naturalWidth > 0,
    assert(s) {
      const bad = [];
      if (!s.hasCard) bad.push("卡片 .capture-panel 没渲染出来");
      else {
        if (!s.shot.present) bad.push("卡片在，但里面没有 <img>");
        else if (!(s.shot.naturalWidth > 0)) bad.push(`图片没解码：${s.shot.src}`);
        if (!s.dish) bad.push("没有菜品名");
        if (s.strip < 1) bad.push("缩略图带是空的");
      }
      return bad;
    },
  },
  samples: {
    title: "管理端 · 门店设置 · 样例数据接入",
    url: "http://127.0.0.1:8000/admin",
    steps: [OPEN_SETTINGS, OPEN_CONNECTIONS],
    probe: `JSON.stringify({
      hasPanel: !!document.querySelector('.sample-intake'),
      heading: document.querySelector('.sample-intake .panel-heading h2')?.textContent || null,
      pick: document.querySelector('.sample-intake .sample-pick')?.textContent?.trim() || null,
      hasFileInput: !!document.querySelector('.sample-intake .sample-pick input[type=file]'),
      runButton: [...document.querySelectorAll('.sample-intake button')].map(b=>b.textContent.trim()).find(t=>t.includes('运行')) || null,
      hint: document.querySelector('.sample-intake .sample-hint')?.textContent?.trim() || null,
      empty: document.querySelector('.sample-intake .sample-empty')?.textContent?.trim() || null,
      rows: document.querySelectorAll('.sample-intake [data-slot=table-body] [data-slot=table-row]').length,
      thumbs: [...document.querySelectorAll('.sample-intake .sample-thumb')]
                 .map(i => ({ src: i.getAttribute('src'), w: i.naturalWidth })),
      loading: !!document.querySelector('.loading-surface')
    })`,
    ready: (s) => s.hasPanel,
    assert(s) {
      const bad = [];
      if (!s.hasPanel) bad.push("卡片 .sample-intake 没渲染出来（门店设置 → 数据接入）");
      else {
        if (!s.hasFileInput) bad.push("没有选图片的 file input");
        if (!s.runButton) bad.push("没有「运行」按钮");
        if (!s.heading) bad.push("没有标题");
        // 有样例时缩略图必须真的解码出来
        for (const t of s.thumbs) if (!(t.w > 0)) bad.push(`样例缩略图没解码：${t.src}`);
      }
      return bad;
    },
  },
};

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

function parseArgs(argv) {
  const out = { check: "capture", url: null };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--check") out.check = argv[++i];
    else if (argv[i] === "--url") out.url = argv[++i];
  }
  return out;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const spec = CHECKS[args.check];
  if (!spec) {
    console.error(`未知的检查项 ${args.check}；可选：${Object.keys(CHECKS).join(" / ")}`);
    return 2;
  }
  const url = args.url || spec.url;
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
  const evaluate = async (expression) => {
    const r = await send("Runtime.evaluate", { expression, returnByValue: true });
    return r.result?.result?.value;
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
      // 页面就绪前点击会落空，所以每一步都重试到生效
      if (i >= 2) for (const step of spec.steps) await evaluate(step);
      const raw = await evaluate(spec.probe);
      if (!raw) continue;
      seen = JSON.parse(raw);
      if (spec.ready(seen)) break;
    }

    console.log(JSON.stringify({ check: args.check, title: spec.title, url, browser, ...seen }, null, 2));
    const failures = spec.assert(seen || {});
    if (failures.length) {
      console.error(`\n核对不通过（${spec.title} ${url}）：\n  - ` + failures.join("\n  - "));
      return 1;
    }
    console.log(`\n核对通过：${spec.title} 渲染出来了`);
    return 0;
  } finally {
    try { ws?.close(); } catch { /* 连接可能已经断了 */ }
    child.kill();
  }
}

process.exit(await main());
