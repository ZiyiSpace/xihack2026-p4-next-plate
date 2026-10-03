// 管理端「样例数据接入」验收：真开浏览器，选图 → 填净重 → 点运行 → 核对结果。
//
//   node modules/integration/check_sample_intake_flow.mjs
//   node modules/integration/check_sample_intake_flow.mjs --keep   # 不复位业务数据
//
// 它走的是用户在页面上的真实路径：文件通过 CDP 塞进 <input type=file>（不弹系统对话框），
// 净重改 value 后派发 focusout（React 的 onBlur 监听 focusout），最后点「运行」。
// 断言看的是工作台口径：取用量对不对、有没有生成补菜任务。
//
// 浏览器按 CHROME_EXE → Edge → Playwright 缓存顺序找；只依赖 Node 内置的 fetch / WebSocket。
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const PORT = 9337;
const BASE = process.env.CAPTURE_BACKEND || "http://127.0.0.1:8000";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const here = dirname(fileURLToPath(import.meta.url));

// B001 → B002 是同一盘小馒头的两次经过：201g → 151g，应当判定取用 50 克
const CASES = [
  { image: resolve(here, "../hotpot_dataset_v0_1/images/B001.png"), weight: 201 },
  { image: resolve(here, "../hotpot_dataset_v0_1/images/B002.png"), weight: 151 },
];

// Radix 的 Tabs 只在 mousedown / focus 上切换，光派发 click() 不动
const ACTIVATE = `(el) => { if (!el) return false;
  for (const type of ['pointerdown','mousedown','pointerup','mouseup','click'])
    el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true,
      button: 0, buttons: 1, pointerType: 'mouse', isPrimary: true }));
  return true; }`;

function findBrowser() {
  const home = process.env.USERPROFILE || process.env.HOME || "";
  return [
    process.env.CHROME_EXE,
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium",
    home && join(home, "AppData/Local/ms-playwright/chromium_headless_shell-1148/chrome-win/headless_shell.exe"),
  ].filter(Boolean).find((p) => existsSync(p)) || null;
}

async function main() {
  if (!existsSync(CASES[0].image)) {
    console.error(`找不到验收用的图片：${CASES[0].image}`);
    return 2;
  }
  const browser = findBrowser();
  if (!browser) {
    console.error("找不到浏览器；用 CHROME_EXE 指定一个 Chromium 系可执行文件");
    return 2;
  }
  if (!process.argv.includes("--keep")) {
    await fetch(`${BASE}/api/maintenance/reset`, { method: "POST" });
  }

  const child = spawn(browser, [
    `--remote-debugging-port=${PORT}`, "--disable-gpu", "--no-sandbox",
    "--no-first-run", `--user-data-dir=${join(tmpdir(), "sample-intake-flow")}`,
    "about:blank",
  ], { stdio: "ignore" });

  let ws, nextId = 1;
  const pending = new Map();
  const send = (method, params = {}) => {
    const id = nextId++;
    ws.send(JSON.stringify({ id, method, params }));
    return new Promise((res) => pending.set(id, { res }));
  };
  const evaluate = async (expression) => {
    const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || "求值失败");
    return r.result?.result?.value;
  };
  const waitFor = async (expr, tries = 60) => {
    for (let i = 0; i < tries; i++) { if (await evaluate(expr)) return true; await sleep(500); }
    return false;
  };

  try {
    let page = null;
    for (let i = 0; i < 40 && !page; i++) {
      try {
        const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
        page = list.find((t) => t.type === "page" && t.webSocketDebuggerUrl);
      } catch { /* 调试端口还没起来 */ }
      if (!page) await sleep(250);
    }
    if (!page) throw new Error(`CDP 端口 ${PORT} 没起来`);

    ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) { pending.get(m.id).res(m); pending.delete(m.id); }
    };
    await send("Page.enable");
    await send("DOM.enable");
    await send("Page.navigate", { url: `${BASE}/admin` });

    const steps = [];
    const fail = (why) => { steps.push(`✗ ${why}`); return 1; };

    if (!await waitFor(`document.querySelectorAll('.nav-item').length > 0`)) return fail("工作台导航没渲染出来");
    await evaluate(`fetch('${BASE}/api/samples',{method:'DELETE'})`);
    const opened = await waitFor(`(()=>{ const a=${ACTIVATE};
      a([...document.querySelectorAll('.nav-item')].find(x=>x.textContent.includes('门店设置')));
      a([...document.querySelectorAll('[data-slot=tabs-trigger]')].find(x=>x.textContent.includes('数据接入')));
      return !!document.querySelector('.sample-intake'); })()`);
    steps.push(`${opened ? "✓" : "✗"} 门店设置 → 数据接入 面板出现`);
    if (!opened) return fail("面板没出现");

    const doc = await send("DOM.getDocument", { depth: -1 });
    const input = await send("DOM.querySelector", {
      nodeId: doc.result.root.nodeId, selector: ".sample-pick input[type=file]",
    });
    await send("DOM.setFileInputFiles", { files: CASES.map((c) => c.image), nodeId: input.result.nodeId });
    const rows = `document.querySelectorAll('.sample-intake [data-slot=table-body] [data-slot=table-row]').length`;
    await waitFor(`${rows} >= ${CASES.length}`);
    const rowCount = await evaluate(rows);
    steps.push(`${rowCount >= CASES.length ? "✓" : "✗"} 选图后出现 ${rowCount} 行（期望 ${CASES.length}）`);
    if (rowCount < CASES.length) return fail("上传后列表没有出现对应行数");

    await evaluate(`(()=>{
      const setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
      const rows=[...document.querySelectorAll('.sample-intake [data-slot=table-body] [data-slot=table-row]')];
      const weights=${JSON.stringify(CASES.map((c) => String(c.weight)))};
      rows.forEach((r,i)=>{ const el=r.querySelector('input[type=number]');
        setter.call(el, weights[i]);
        el.dispatchEvent(new Event('input',{bubbles:true}));
        el.dispatchEvent(new FocusEvent('focusout',{bubbles:true})); });
    })()`);
    await sleep(2500);
    const hint = await evaluate(`document.querySelector('.sample-intake .sample-hint')?.textContent || ""`);
    steps.push(`${hint.includes(`${CASES.length}/${CASES.length}`) ? "✓" : "✗"} 填净重生效：${hint}`);

    await evaluate(`(()=>{const b=[...document.querySelectorAll('.sample-intake button')].find(x=>x.textContent.includes('运行'));b.click()})()`);
    const phases = new Set();
    for (let i = 0; i < 90; i++) {
      await sleep(1000);
      const label = (await evaluate(`document.querySelector('.sample-intake button')?.textContent?.trim()`)) || "";
      if (label && label !== "运行") phases.add(label.replace(/\s*\d+\/\d+$/, " n/n"));
      if (await evaluate(`document.querySelectorAll('.sample-intake .sample-state.is-done').length >= ${CASES.length}`)) break;
    }
    steps.push(`✓ 运行经过的阶段：${[...phases].join(" → ") || "(没观察到中间态)"}`);

    const statuses = JSON.parse(await evaluate(
      `JSON.stringify([...document.querySelectorAll('.sample-intake .sample-status')].map(e=>e.innerText))`));
    steps.push(`✓ 每条结果：${statuses.join(" | ")}`);

    const state = JSON.parse(await evaluate(
      `(async()=>{const s=await (await fetch('${BASE}/api/state')).json();
        return JSON.stringify({dishes:s.dishes.map(d=>({name:d.name,takeG:d.takeG,weightG:d.weightG,status:d.status})),
          tasks:s.tasks.length, captures:(s.captures||[]).length})})()`));
    steps.push(`✓ 工作台口径：${JSON.stringify(state.dishes)}，补菜任务 ${state.tasks} 个，抓拍 ${state.captures} 张`);

    // 201 - 151 = 50 克取用；同一道菜应当合成一个盘，并因余量偏低派单
    const bun = state.dishes.find((d) => Math.abs(d.takeG - 50) < 0.5);
    if (!bun) {
      console.log(steps.join("\n"));
      return fail(`工作台没算出 50 克取用（拿到 ${JSON.stringify(state.dishes)}）`);
    }
    if (state.tasks < 1) {
      console.log(steps.join("\n"));
      return fail("余量降到阈值以下，却没有生成补菜任务");
    }

    if (CASES.length !== statuses.length) {
      console.log(steps.join("\n"));
      return fail(`列表行数与结果条数对不上：${statuses.length}`);
    }

    console.log(steps.join("\n"));
    console.log("\n核对通过：从界面选图、填净重、点运行，取用与补菜任务都算出来了");
    return 0;
  } finally {
    try { ws?.close(); } catch { /* 连接可能已经断了 */ }
    child.kill();
  }
}

process.exit(await main());
