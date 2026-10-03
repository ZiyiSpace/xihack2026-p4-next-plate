// 「上盘更换期限」与数据来源下拉的界面验收：真开一个浏览器，点开真控件，读真 DOM。
//
//   node modules/integration/check_replace_deadline.mjs
//
// 三件事，都是只有真渲染才能证明的：
//   1. 右上角「数据来源」下拉不能再出现滚动条 —— Radix 在 item-aligned 定位下会因为
//      残留的 1px scrollTop 而**渲染**一条 24px 的上滚箭头（不是 CSS 能藏掉的东西，
//      它压根不进 DOM）。所以断言看的是「滚动按钮不存在」+「scrollTop 归零」，
//      光看有没有滚动条样式是测不出来的。
//   2. 门店设置 → 菜品规则要有「上盘更换期限」一列。
//   3. 后厨补菜要有换菜单，而且措辞是「换下并上新」而不是「完成补菜」。
//
// 前置：后端跑在 :8000，且库里当前有一张超期的 replace 待办单
//       （modules/simulator 回放 + 把某道菜的更换期限调小即可造出来）。
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const EVIDENCE = join(HERE, "evidence");
const PORT = 9337;
const BASE = process.env.WORKBENCH_BASE || "http://127.0.0.1:8000";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const home = process.env.USERPROFILE || process.env.HOME || "";

// Radix 的 Tabs / 菜单只在 mousedown 或 focus 上切换，光派发 click() 不生效。
const ACTIVATE = `(el) => {
  if (!el) return false;
  for (const type of ['pointerdown','mousedown','pointerup','mouseup','click'])
    el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true,
      button: 0, buttons: 1, pointerType: 'mouse', isPrimary: true }));
  return true;
}`;

const findBrowser = () => [
  process.env.CHROME_EXE,
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium",
  home && join(home, "AppData/Local/ms-playwright/chromium-1148/chrome-win/chrome.exe"),
].filter(Boolean).find((p) => existsSync(p)) || null;

// ---- 1. 数据来源下拉 ----
const OPEN_SOURCE = `(() => { const a=${ACTIVATE};
  return a([...document.querySelectorAll('[data-slot=select-trigger]')]
    .find(x=>x.getAttribute('aria-label')==='数据来源')); })()`;
const SELECT_SOURCE_PROBE = `(() => {
  const content=document.querySelector('[data-slot=select-content]');
  if(!content) return JSON.stringify({open:false});
  const vp=content.querySelector('[data-radix-select-viewport]');
  const wrap=content.parentElement;
  return JSON.stringify({
    open:true,
    scrollButtons:[...content.querySelectorAll(
      '[data-slot=select-scroll-up-button],[data-slot=select-scroll-down-button]')]
      .map(b=>b.getAttribute('data-slot')),
    viewport:{clientH:vp.clientHeight, scrollH:vp.scrollHeight, scrollTop:vp.scrollTop},
    contentOverflow: content.scrollHeight - content.clientHeight,
    wrapperHeight: wrap.style.height,
    itemCount: content.querySelectorAll('[data-slot=select-item]').length,
    itemsVisible: [...content.querySelectorAll('[data-slot=select-item]')].every(i=>{
      const r=i.getBoundingClientRect(), c=content.getBoundingClientRect();
      return r.top>=c.top-0.5 && r.bottom<=c.bottom+0.5;
    })
  });
})()`;

// ---- 2. 门店设置 → 菜品规则 ----
const OPEN_SETTINGS = `(() => { const a=${ACTIVATE};
  return a([...document.querySelectorAll('.nav-item')].find(x=>x.textContent.includes('门店设置'))); })()`;
const OPEN_DISH_RULES = `(() => { const a=${ACTIVATE};
  return a([...document.querySelectorAll('[data-slot=tabs-trigger]')]
    .find(x=>x.textContent.includes('菜品规则'))); })()`;
const DISH_RULES_PROBE = `(() => {
  const heads=[...document.querySelectorAll('[data-slot=table-header] [data-slot=table-head]')]
    .map(h=>h.textContent.trim());
  const rows=[...document.querySelectorAll('[data-slot=table-body] [data-slot=table-row]')]
    .map(r=>[...r.querySelectorAll('[data-slot=table-cell]')].map(c=>c.textContent.trim()));
  const at=heads.indexOf('上盘更换期限');
  return JSON.stringify({open:!!heads.length, heads, replaceColumn: at,
    replaceCells: at<0?[]:rows.map(r=>r[at]), rowCount: rows.length});
})()`;

// ---- 3. 后厨补菜 ----
const KITCHEN_PROBE = `(() => {
  const rows=[...document.querySelectorAll('.task-row')];
  const cards=rows.map(r=>({
    dish:r.querySelector('.task-dish h2')?.textContent?.trim()||null,
    badge:r.querySelector('.task-dish .status')?.textContent?.trim()||null,
    state:r.querySelector('.task-dish p')?.textContent?.trim()||null,
    quantity:r.querySelector('.task-quantity')?.textContent?.trim()||null,
    button:r.querySelector('.task-row-actions button')?.textContent?.trim()||null,
  }));
  // 非补菜任务的 quantity 是 null，曾经把「已结束任务」表格里的 qty() 送进
  // toLocaleString 直接崩掉整页 —— 这里显式盯住，别再退回那个状态。
  return JSON.stringify({open:rows.length>0, rowCount:rows.length, cards,
    crashed:/This page couldn|toLocaleString|vite-error-overlay/.test(document.body.innerText)
            || !!document.querySelector('vite-error-overlay')});
})()`;

async function main() {
  const browser = findBrowser();
  if (!browser) { console.error("找不到浏览器；用 CHROME_EXE 指定一个 Chromium 系可执行文件"); return 2; }
  mkdirSync(EVIDENCE, { recursive: true });
  const child = spawn(browser, [`--remote-debugging-port=${PORT}`, "--disable-gpu", "--no-sandbox",
    "--no-first-run", `--user-data-dir=${join(tmpdir(), "check-replace")}`, "about:blank"],
    { stdio: "ignore" });
  let ws, nextId = 1; const pending = new Map();
  const send = (method, params = {}) => { const id = nextId++;
    ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => pending.set(id, { resolve, reject })); };
  const evaluate = async (expression) => (await send("Runtime.evaluate",
    { expression, returnByValue: true, awaitPromise: true })).result?.result?.value;
  const shot = async (name, clip) => {
    // clip 是相对整个页面的坐标，所以滚过页之后要补上 scrollY，并允许截到视口外
    const r = await send("Page.captureScreenshot", clip
      ? { format: "png", captureBeyondViewport: true, clip: { ...clip, scale: 2 } }
      : { format: "png" });
    const path = join(EVIDENCE, name);
    writeFileSync(path, Buffer.from(r.result.data, "base64"));
    return path;
  };
  const goto = async (path, waitFor) => {
    await send("Page.navigate", { url: BASE + path });
    for (let i = 0; i < 40; i++) { await sleep(500);
      if (await evaluate(waitFor)) return true; }
    return false;
  };

  const failures = [];
  const report = [];
  try {
    let page = null;
    for (let i = 0; i < 40 && !page; i++) {
      try { const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
        page = list.find((t) => t.type === "page" && t.webSocketDebuggerUrl); } catch { /* 端口还没起 */ }
      if (!page) await sleep(250);
    }
    if (!page) throw new Error(`CDP 端口 ${PORT} 没起来`);
    ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) { pending.get(m.id).resolve(m); pending.delete(m.id); } };
    await send("Page.enable");
    await send("Emulation.setDeviceMetricsOverride",
      { width: 1440, height: 900, deviceScaleFactor: 2, mobile: false });

    // ---------------- 1 ----------------
    // 原缺陷是「有概率」出现，跟窗口尺寸有关，所以逐个尺寸都量一遍
    const sizes = [[1440, 900], [1280, 800], [1920, 1080], [1366, 768], [1100, 700]];
    const sizeResults = [];
    let shot1 = null;
    for (const [w, h] of sizes) {
      await send("Emulation.setDeviceMetricsOverride",
        { width: w, height: h, deviceScaleFactor: 2, mobile: false });
      await goto("/admin", `!!document.querySelector('[aria-label="数据来源"]')`);
      let seen = null;
      for (let i = 0; i < 20 && !seen?.open; i++) {
        if (!await evaluate(`!!document.querySelector('[data-slot=select-content]')`))
          await evaluate(OPEN_SOURCE);
        await sleep(300);
        seen = JSON.parse(await evaluate(SELECT_SOURCE_PROBE) || "{}");
      }
      sizeResults.push({ size: `${w}x${h}`, ...seen });
      const bad = [];
      if (!seen?.open) bad.push("下拉没打开");
      else {
        if (seen.scrollButtons.length)
          bad.push(`渲染出了滚动按钮：${seen.scrollButtons.join(",")}`);
        if (seen.viewport.scrollTop !== 0)
          bad.push(`下拉被滚动了 scrollTop=${seen.viewport.scrollTop}`);
        if (seen.contentOverflow > 0) bad.push(`内容溢出 ${seen.contentOverflow}px`);
        if (!seen.itemsVisible) bad.push("有选项没完整露出来");
        if (seen.itemCount !== 2) bad.push(`选项数不是 2 而是 ${seen.itemCount}`);
      }
      if (bad.length) failures.push(`${w}x${h} 数据来源下拉：${bad.join("；")}`);
      if (w === 1440) shot1 = await shot("验收-数据来源下拉.png", { x: 940, y: 0, width: 420, height: 300 });
      await evaluate(`document.body.click()`);
      await sleep(200);
    }
    await send("Emulation.setDeviceMetricsOverride",
      { width: 1440, height: 900, deviceScaleFactor: 2, mobile: false });
    report.push({ check: "数据来源下拉（各尺寸）", seen: sizeResults, screenshot: shot1 });

    // ---------------- 2 ----------------
    await goto("/admin", `!!document.querySelector('.nav-item')`);
    for (let i = 0; i < 20; i++) {
      await evaluate(OPEN_SETTINGS); await evaluate(OPEN_DISH_RULES); await sleep(300);
      const p = JSON.parse(await evaluate(DISH_RULES_PROBE) || "{}");
      if (p.replaceColumn >= 0) break;
    }
    const rules = JSON.parse(await evaluate(DISH_RULES_PROBE) || "{}");
    const shot2 = await shot("验收-菜品规则更换期限.png", { x: 260, y: 0, width: 1180, height: 560 });
    report.push({ check: "菜品规则", seen: rules, screenshot: shot2 });
    if (rules.replaceColumn < 0) failures.push(`菜品规则表没有「上盘更换期限」列：${rules.heads?.join(" / ")}`);
    else if (!rules.replaceCells.every((c) => /^\d+ 分钟$/.test(c)))
      failures.push(`更换期限单元格不是分钟数：${JSON.stringify(rules.replaceCells)}`);

    // ---------------- 3 ----------------
    await goto("/kitchen", `!!document.querySelector('.task-row')`);
    const kitchen = JSON.parse(await evaluate(KITCHEN_PROBE) || "{}");
    // 把换菜单滚进视野再截，否则截到的是页面顶部的抓拍卡片
    const box = JSON.parse(await evaluate(`(() => {
      const card=[...document.querySelectorAll('.task-row')]
        .find(r=>r.querySelector('.task-dish .status')?.textContent.trim()==='更换菜品');
      if(!card) return "null";
      card.scrollIntoView({block:'center'});
      const r=card.getBoundingClientRect();
      return JSON.stringify({x:260, y:r.top+window.scrollY-70,
                             width:1180, height:r.height+150});
    })()`) || "null");
    await sleep(400);
    const shot3 = box
      ? await shot("验收-后厨换菜单.png", box)
      : await shot("验收-后厨换菜单.png", { x: 260, y: 0, width: 1180, height: 620 });
    report.push({ check: "后厨补菜", seen: kitchen, screenshot: shot3 });
    const replaceCard = (kitchen.cards || []).find((c) => c.badge === "更换菜品");
    // 非补菜任务的 quantity 是 null，历史上一进「已结束任务」的 qty() 就把整页崩掉
    if (kitchen.crashed) failures.push("后厨页面报了运行时错误（曾因 qty(null) 崩页）");
    if (!replaceCard) failures.push(
      `后厨没有换菜单；现有卡片 ${JSON.stringify((kitchen.cards || []).map(c => c.badge))}`);
    else {
      // 待处理的换菜单按钮是「开始更换」，做完才变成「换下并上新」。
      // 这里只断言待处理态，关键是它不能沿用补菜那套措辞。
      if (replaceCard.button !== "开始更换")
        failures.push(`待处理换菜单按钮措辞是「${replaceCard.button}」，应为「开始更换」`);
      if (/完成补菜|开始制作/.test(replaceCard.button || ""))
        failures.push(`换菜单沿用了补菜措辞：「${replaceCard.button}」`);
      if (replaceCard.quantity && /克|件|千克/.test(replaceCard.quantity))
        failures.push(`换菜单不该显示补充分量，实际显示「${replaceCard.quantity}」`);
    }

    console.log(JSON.stringify(report, null, 2));
    if (failures.length) {
      console.error("\n验收不通过：\n  - " + failures.join("\n  - "));
      return 1;
    }
    console.log(`\n验收通过：下拉无滚动条、菜品规则有更换期限、后厨有换菜单（${EVIDENCE}）`);
    return 0;
  } finally {
    try { ws?.close(); } catch { /* 连接可能已经断了 */ }
    child.kill();
  }
}
process.exit(await main());
