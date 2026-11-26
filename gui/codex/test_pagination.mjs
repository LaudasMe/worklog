// 在临时 Chromium 用户目录中验证 HTML 分页。面板宿主和任务数据使用合成样本。
import { strict as assert } from "node:assert";
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const [chromium, panelPath, screenshotDir] = process.argv.slice(2);
if (!chromium || !panelPath) {
  throw new Error("Usage: node gui/codex/test_pagination.mjs <Chromium> <panel.html> [existing screenshot directory]");
}

const task = (project, number, status) => ({
  id: project + number, number: "T" + String(number).padStart(3, "0"),
  title: project + " task " + number, status, lifecycle: status, revision: 1,
  updatedAt: "2026-11-25T05:00:00Z",
  priority: ["p0","p1","p2","p3",null][(number - 1) % 5],
});
const series = (id, first, count, status) => Array.from({ length: count }, (_, i) => task(id, first + i, status));
const project = (id, projectName, tasks) => ({
  state: "ready", id, projectName, tasks, writable: true, path: "/fixtures/" + id,
  generation: "generation-" + id,
});
const projectA = project("A", "甲项目", [
  task("A", 26, "TODO"), ...series("A", 1, 25, "IN_PROGRESS"),
  ...series("A", 43, 3, "DONE"), ...series("A", 27, 16, "TODO"),
]);
const projectB = project("B", "乙项目", [
  ...series("B", 1, 12, "IN_PROGRESS"), ...series("B", 13, 9, "TODO"), ...series("B", 22, 2, "DONE"),
]);
const unreadable = { state: "error", path: "/fixtures/missing", projectName: "不可读项目", message: "合成读取失败" };
const fixture = [projectA, unreadable, projectB];
const source = readFileSync(panelPath, "utf8");
const directory = mkdtempSync(join(tmpdir(), "worklog-pagination-"));
writeFileSync(join(directory, "project.html"), source);
writeFileSync(join(directory, "dashboard.html"), source.replace('data-surface="project"', 'data-surface="dashboard"'));
writeFileSync(join(directory, "host.html"), [
  '<!doctype html><meta charset="utf-8"><style>body{margin:0}iframe{display:block;width:100vw;height:100vh;border:0}</style>',
  '<iframe id="frame"></iframe><script>',
  'const dashboard = location.search.includes("dashboard");',
  'const language = new URLSearchParams(location.search);',
  'const hostContext = {theme: dashboard ? "light" : "dark", locale: language.get("host") ?? (language.has("system") ? undefined : "zh-CN"), systemLocale: language.get("system")};',
  'const projects = ' + JSON.stringify(fixture) + ';',
  'const frame = document.getElementById("frame");',
  'const view = () => dashboard ? {state: "ready", projects} : projects[0];',
  'const post = (message) => frame.contentWindow.postMessage({jsonrpc: "2.0", ...message}, "*");',
  'window.addEventListener("message", ({data: message}) => {',
  '  if (message.method === "ui/initialize") {',
  '    post({id: message.id, result: {hostContext}});',
  '  } else if (message.method === "ui/notifications/initialized") {',
  '    post({method: "ui/notifications/tool-result", params: {structuredContent: view()}});',
  '  } else if (message.method === "tools/call") {',
  '    const {name, arguments: args} = message.params;',
  '    let result;',
  '    if (name.startsWith("open_worklog_")) result = view();',
  '    else if (name === "mutate_worklog_task" && args.command === "task-create") {',
  '      const project = dashboard ? projects.find((item) => item.id === args.projectId) : projects[0];',
  '      if (args.request.project_id !== project.id || args.request.data_generation !== project.generation) throw new Error("Wrong project identity");',
  '      const task = {id: "new", number: "T999", title: args.request.title, status: "TODO", lifecycle: "TODO", revision: 1};',
  '      project.tasks.push(task);',
  '      result = {state: "ready", task};',
  '    } else if (name === "mutate_worklog_task" && args.command === "task-set-priority") {',
  '      const project = dashboard ? projects.find((item) => item.id === args.projectId) : projects[0];',
  '      const task = project.tasks.find((item) => item.id === args.request.task_id);',
  '      if (args.request.project_id !== project.id || args.request.data_generation !== project.generation || task.revision !== args.request.expected_revision) throw new Error("Wrong write guards");',
  '      task.priority = args.request.priority;',
  '      task.revision += 1;',
  '      result = {state: "ready", task};',
  '    } else if (name === "read_worklog_task_sections") {',
  '      result = {state:"ready", todos:{open:[],processed:[]},history:[],sessions:[]};',
  '    } else throw new Error("Unexpected tool: " + name);',
  '    post({id: message.id, result: {structuredContent: result}});',
  '  }',
  '});',
  'frame.src = dashboard ? "dashboard.html" : "project.html";',
  '</script>',
].join("\n"));

const browser = spawn(chromium, [
  "--headless", "--disable-gpu", "--allow-file-access-from-files", "--disable-background-networking",
  "--user-data-dir=" + join(directory, "profile"), "--remote-debugging-pipe", "about:blank",
], { stdio: ["ignore", "ignore", "pipe", "pipe", "pipe"] });
const pending = new Map();
const exceptions = [];
let sequence = 0;
let buffer = "";
let stderr = "";
browser.stderr.on("data", (chunk) => { stderr += chunk; });
browser.stdio[4].setEncoding("utf8");
browser.stdio[4].on("data", (chunk) => {
  const packets = (buffer + chunk).split("\0");
  buffer = packets.pop();
  for (const packet of packets) {
    const response = JSON.parse(packet);
    const waiting = pending.get(response.id);
    if (waiting) {
      pending.delete(response.id);
      if (response.error) waiting.reject(new Error(JSON.stringify(response.error)));
      else waiting.resolve(response.result);
    } else if (response.method === "Runtime.exceptionThrown") {
      exceptions.push(response.params.exceptionDetails);
    }
  }
});
const browserExited = new Promise((resolve) => {
  browser.once("error", (error) => {
    for (const { reject } of pending.values()) reject(error);
  });
  browser.once("close", (code, signal) => {
    for (const { reject } of pending.values()) reject(new Error("Chromium exited: " + code + "/" + signal + "\n" + stderr));
    resolve();
  });
});
const timeout = setTimeout(() => browser.kill(), 45000);
const send = (method, params, sessionId) => new Promise((resolve, reject) => {
  const id = ++sequence;
  pending.set(id, { resolve, reject });
  browser.stdio[3].write(JSON.stringify({ id, method, params, sessionId }) + "\0", (error) => {
    if (error) reject(error);
  });
});

try {
  const { targetInfos } = await send("Target.getTargets");
  const target = targetInfos.find(({ type }) => type === "page");
  const { sessionId } = await send("Target.attachToTarget", { targetId: target.targetId, flatten: true });
  const call = (method, params) => send(method, params, sessionId);
  await call("Runtime.enable");
  await call("Emulation.setFocusEmulationEnabled", { enabled: true });
  const evaluate = async (expression) => {
    const reply = await call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (reply.exceptionDetails) throw new Error(reply.exceptionDetails.exception?.description || reply.exceptionDetails.text);
    return reply.result.value;
  };
  const inside = (script) => evaluate("document.querySelector('iframe').contentWindow.eval(" + JSON.stringify(script) + ")");
  const wait = async (check, label) => {
    for (let attempt = 0; attempt < 100; attempt++) {
      if (await check()) return;
      await new Promise((resolve) => setTimeout(resolve, 30));
    }
    throw new Error("Timed out waiting for " + label);
  };
  const query = (selector) => "document.querySelector(" + JSON.stringify(selector) + ")";
  const click = (selector) => inside(query(selector) + ".click()");
  const text = (selector) => inside(query(selector) + ".textContent");
  const rowKeys = () => inside("[...document.querySelectorAll('#tasks .task')].map((row) => row.dataset.key)");
  const groupLabels = () => inside("[...document.querySelectorAll('.group-heading')].map((group) => [group.querySelector('span').textContent, group.querySelector('.count').textContent])");
  const selectProject = (value) => inside("{ const select = document.getElementById('project-filter'); select.value = " +
    JSON.stringify(value) + "; select.dispatchEvent(new Event('change', {bubbles:true})); }");
  const filter = (key) => click('.filter[data-status="' + key + '"]');
  const priorityFilter = (key) => click('#priority-filters .filter[data-priority="' + key + '"]');
  const pageState = async (page, pages, range, total) => {
    assert.deepEqual(await Promise.all([
      text("#page-number"), text("#page-count"), text("#page-range"), text("#page-task-count"),
      inside("document.querySelector('#previous-page').disabled"), inside("document.querySelector('#next-page').disabled"),
    ]), [String(page), String(pages), range, String(total), page === 1, page === pages]);
  };
  const load = async (surface, width, height) => {
    await call("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
    await call("Page.navigate", { url: pathToFileURL(join(directory, "host.html")).href + "?" + surface });
    await wait(() => evaluate("Boolean(document.querySelector('iframe')?.contentDocument?.querySelector('#tasks-view:not([hidden])'))"), surface);
  };
  const verifyLayout = async (surface) => {
    const layout = () => inside("(() => { const list = document.querySelector('#task-list-viewport'); " +
      "const bar = document.querySelector('#pagination').getBoundingClientRect(); " +
      "const [left,right] = ['#previous-page','#next-page'].map((s) => document.querySelector(s).getBoundingClientRect()); " +
      "const range = document.querySelector('#page-range').getBoundingClientRect(); " +
      "return {top:bar.top,bottom:bar.bottom,left:bar.left,right:bar.right," +
      "listBottom:list.getBoundingClientRect().bottom,scroll:list.scrollTop," +
      "contained:range.right < left.left && right.right <= bar.right," +
      "viewport:bar.bottom <= window.innerHeight && bar.right <= window.innerWidth}; })()");
    const before = await layout();
    assert.ok(before.viewport && before.contained, surface + " pager is visible and fits in the viewport");
    assert.equal(before.listBottom, before.top);
    for (const [id, label] of [["#previous-page", "上一页"], ["#next-page", "下一页"]]) {
      assert.equal((await text(id)).trim(), "");
      assert.equal(await inside(query(id) + ".getAttribute('aria-label')"), label);
    }
    await call("Input.dispatchMouseEvent", {
      type: "mouseWheel", x: before.left + 100, y: before.top - 25, deltaX: 0, deltaY: 200,
    });
    await wait(async () => (await layout()).scroll > 0, surface + " list wheel scroll");
    const after = await layout();
    assert.deepEqual([after.top, after.bottom], [before.top, before.bottom], surface + " pager stays fixed");
    if (screenshotDir) {
      const { data } = await call("Page.captureScreenshot", { format: "png" });
      writeFileSync(join(screenshotDir, surface + "-pagination.png"), Buffer.from(data, "base64"));
    }
  };
  const keypress = async (key, code, keyCode, character) => {
    const event = { key, code, windowsVirtualKeyCode: keyCode };
    await call("Input.dispatchKeyEvent", { ...event, type: "keyDown", ...(character ? {text: character} : {}) });
    await call("Input.dispatchKeyEvent", { ...event, type: "keyUp" });
  };
  const verifyPriority = async () => {
    const priority = '.task[data-key="A/A1"] .task-priority';
    const details = '.task[data-key="A/A1"] .priority-value';
    assert.equal(await text(details), "P0 最高");
    assert.deepEqual(await inside(
      "[...new Set([...document.querySelectorAll('.task-priority .priority-mark')].map(mark => mark.dataset.priority))].sort()"
    ), ["p0", "p1", "p2", "p3", "unset"]);
    assert.equal(await inside(query(priority) + ".getAttribute('aria-label')"), "T001 优先级：P0 最高");
    assert.equal(await inside(`(() => {
      const control = ${query(priority)};
      const row = control.closest(".task");
      const number = row.querySelector(".task-number").getBoundingClientRect();
      const badge = control.getBoundingClientRect();
      return number.right < badge.left && badge.right < window.innerWidth && badge.width > 20;
    })()`), true);
    const checkPriorityCenter = async (state) => {
      const offset = await inside(`(() => {
        const button = ${query(priority)};
        const box = button.getBoundingClientRect();
        const mark = button.querySelector(".priority-mark").getBoundingClientRect();
        return [(mark.left + mark.right - box.left - box.right) / 2,
          (mark.top + mark.bottom - box.top - box.bottom) / 2];
      })()`);
      assert.ok(offset.every((shift) => Math.abs(shift) < 0.5),
        `priority mark is centered ${state}: ${offset}`);
    };
    const moveTo = async (selector) => {
      const position = await inside(`(() => {
        const button = ${query(selector)};
        button.scrollIntoView({block: "nearest"});
        const box = button.getBoundingClientRect();
        return {x: box.left + box.width / 2, y: box.top + box.height / 2};
      })()`);
      await call("Input.dispatchMouseEvent", {type: "mouseMoved", ...position});
      await wait(() => inside(query(selector) + ".matches(':hover')"), selector + " hover");
      return position;
    };
    const press = async (position) => {
      for (const type of ["mousePressed", "mouseReleased"]) {
        await call("Input.dispatchMouseEvent", {type, ...position, button: "left", clickCount: 1});
      }
    };
    const pointer = await moveTo(priority);
    await checkPriorityCenter("on hover");
    await press(pointer);
    assert.equal(await inside(query(priority) + ".getAttribute('aria-expanded')"), "true");
    assert.equal(await inside("document.querySelectorAll('#status-menu [role=menuitemradio]').length"), 5);
    assert.equal(await inside("document.activeElement.dataset.priority"), "p0");
    assert.equal(await inside("document.activeElement.querySelector('svg:last-child use')?.getAttribute('href')"), "#icon-check");
    await keypress("ArrowDown", "ArrowDown", 40);
    await keypress("Enter", "Enter", 13, "\r");
    await wait(async () => (await text(details)) === "P1 高", "priority saved");
    assert.equal(await inside("document.activeElement.className"), "task-priority");
    await press(await moveTo(priority));
    const unsetPoint = await moveTo('#status-menu [data-priority="unset"]');
    assert.equal(await inside(query(priority) + ".matches(':hover')"), false);
    await checkPriorityCenter("with the menu open");
    await press(unsetPoint);
    await wait(async () => (await text(details)) === "未设置", "priority cleared");
    assert.equal(await inside(query(priority) + ".getAttribute('aria-label')"), "T001 优先级：未设置");
    await moveTo(priority);
    await checkPriorityCenter("on hover with no priority set");
  };
  const checkFullOrder = async (expected, size) => {
    const seen = [];
    for (let start = 0; start < expected.length; start += size) {
      if (start) await click("#next-page");
      const keys = await rowKeys();
      assert.deepEqual(keys, expected.slice(start, start + size));
      seen.push(...keys);
      if (start) assert.equal(await inside("document.querySelector('#task-list-viewport').scrollTop"), 0);
    }
    assert.deepEqual(seen, expected);
    assert.equal(new Set(seen).size, expected.length);
  };
  const add = async (key) => {
    await click("#add-task");
    await inside("document.querySelector('#dialog-fields input').value = '新增分页任务';");
    await click("#dialog-submit");
    await wait(async () => (await text("#refresh-status")) === "已添加 T999。", "create confirmed");
    assert.equal(await inside("document.activeElement.closest('.task')?.dataset.key"), key);
  };
  const refresh = async () => {
    await click("#refresh");
    await wait(async () => (await text("#refresh-status")) === "已刷新任务。", "refresh confirmed");
  };

  await load("dashboard", 1080, 720);
  assert.equal(await inside("document.documentElement.dataset.theme"), "light");
  await pageState(1, 4, "1–20", 63);
  assert.deepEqual(await groupLabels(), [["甲项目", "42"]]);
  await verifyLayout("dashboard");
  await verifyPriority();
  const dashboardOrder = [projectA, projectB].flatMap(({ id, tasks }) =>
    tasks.filter(({ status }) => status !== "DONE").map((item) => id + "/" + item.id));
  await checkFullOrder(dashboardOrder, 20);
  await pageState(4, 4, "61–63", 63);
  await click("#next-page");
  assert.deepEqual(await rowKeys(), dashboardOrder.slice(-3));
  await click("#previous-page");
  assert.deepEqual(await groupLabels(), [["甲项目", "42"], ["不可读项目", "0"], ["乙项目", "21"]]);
  assert.equal(await text(".group-note"), "合成读取失败");
  await click("#previous-page");
  assert.deepEqual(await groupLabels(), [["甲项目", "42"]]);
  await filter("IN_PROGRESS");
  await pageState(1, 2, "1–20", 37);
  await click("#next-page");
  await selectProject("A");
  await pageState(1, 2, "1–20", 25);
  await filter("DONE");
  await pageState(1, 1, "1–3", 3);
  await filter("PAUSED");
  assert.equal(await inside("document.querySelector('#pagination').hidden"), true);
  assert.equal(await text("#empty-tasks-message"), "当前筛选下暂无任务。");
  assert.equal(await inside("document.querySelector('#show-all').hidden"), false);
  await click("#show-all");
  await pageState(1, 3, "1–20", 45);
  await selectProject("B");
  await filter("DONE");
  await add("B/new");
  assert.equal(await inside("document.querySelector('#project-filter').value"), "B");
  await pageState(2, 2, "21–22", 22);
  await refresh();
  await pageState(2, 2, "21–22", 22);
  await selectProject("");
  await click("#next-page");
  await click("#next-page");
  await click("#next-page");
  await evaluate("projects[0].tasks = projects[0].tasks.slice(0, 3);");
  await refresh();
  await pageState(2, 2, "21–25", 25);

  await load("project", 320, 610);
  assert.equal(await inside("document.documentElement.dataset.theme"), "dark");
  await pageState(1, 4, "1–12", 45);
  assert.deepEqual(await groupLabels(), [["进行中", "25 项"]]);
  await verifyLayout("sidebar");
  await verifyPriority();
  const sidebarOrder = ["IN_PROGRESS", "TODO", "DONE"].flatMap((status) =>
    projectA.tasks.filter((item) => item.status === status).map((item) => "A/" + item.id));
  await checkFullOrder(sidebarOrder, 12);
  await pageState(4, 4, "37–45", 45);
  await click("#previous-page");
  assert.deepEqual(await groupLabels(), [["进行中", "25 项"], ["待开始", "17 项"]]);
  await click("#previous-page");
  assert.deepEqual(await groupLabels(), [["进行中", "25 项"]]);
  await filter("IN_PROGRESS");
  await pageState(1, 3, "1–12", 25);
  await inside("document.querySelector('#next-page').focus()");
  assert.equal(await inside("document.activeElement.id"), "next-page");
  const enter = { key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 };
  await call("Input.dispatchKeyEvent", { ...enter, type: "keyDown", text: "\r" });
  await call("Input.dispatchKeyEvent", { ...enter, type: "keyUp" });
  await wait(async () => (await text("#page-number")) === "2", "keyboard pagination");
  await pageState(2, 3, "13–24", 25);
  await filter("DONE");
  await pageState(1, 1, "1–3", 3);
  await add("A/new");
  await pageState(2, 2, "13–18", 18);
  await refresh();
  await pageState(2, 2, "13–18", 18);
  await evaluate("projects[0].tasks = projects[0].tasks.filter((task) => task.status !== 'TODO');");
  await refresh();
  assert.equal(await inside("document.querySelector('#pagination').hidden"), true);
  assert.equal(await text("#empty-tasks-message"), "当前筛选下暂无任务。");
  await evaluate("projects[0].tasks = [];");
  await refresh();
  assert.equal(await inside("document.querySelector('#pagination').hidden"), true);
  assert.equal(await text("#empty-tasks-message"), "此项目还没有任务。");

  for (const dashboard of [true, false]) {
    await load(dashboard ? "dashboard" : "project", dashboard ? 1080 : 320, 650);
    await filter("IN_PROGRESS");
    await click("#next-page");
    await priorityFilter("p1");
    let count = dashboard ? 8 : 5;
    await pageState(1, 1, "1–" + count, count);
    assert.equal(await text('#priority-filters [data-priority="p1"] .count'), String(count));
    assert.equal(await text('#priority-filters [data-priority="all"] .count'), dashboard ? "37" : "25");
    assert.equal(await text('#filters [data-status="DONE"] .count'), dashboard ? "1" : "0");
    assert.equal(await inside("[...document.querySelectorAll('.task-priority .priority-mark')].every(mark => mark.dataset.priority === 'p1')"), true);
    const first = '.task[data-key="' + (await rowKeys())[0] + '"] .task-priority';
    await click(first);
    await click('#status-menu [data-priority="unset"]');
    await wait(async () => (await rowKeys()).length === count - 1, "edited task leaves the priority filter");
    await priorityFilter("unset");
    count = dashboard ? 8 : 6;
    await pageState(1, 1, "1–" + count, count);
    assert.equal(await text('#filters [data-status="IN_PROGRESS"] .count'), String(count));
    await filter("CANCELLED");
    assert.equal(await text("#empty-tasks-message"), "当前筛选下暂无任务。");
    await click("#show-all");
    if (dashboard) await selectProject("B");
    await filter("IN_PROGRESS");
    await priorityFilter("p1");
    await add(dashboard ? "B/new" : "A/new");
    assert.equal(await inside("document.querySelector('#priority-filters [aria-pressed=true]').dataset.priority"), "all");
    await pageState(2, 2, dashboard ? "21–22" : "13–18", dashboard ? 22 : 18);
  }
  for (const surface of ["dashboard", "project"]) {
    for (const [parameters, expected] of [
      ["host=en-US&system=zh-CN", "en"],
      ["host=zh-Hans&system=en-US", "zh-Hans"],
      ["system=zh-CN", "zh-Hans"],
      ["host=ja-JP&system=zh-CN", "en"],
    ]) {
      await load(surface + "&" + parameters, 320, 650);
      assert.equal(await inside("document.documentElement.lang"), expected);
      assert.equal((await text("#refresh")).trim(), expected === "en" ? "Refresh" : "刷新");
      assert.equal(await text('#filters [data-status="IN_PROGRESS"]'), expected === "en"
        ? "In progress" + (surface === "dashboard" ? 37 : 25) : "进行中" + (surface === "dashboard" ? 37 : 25));
      assert.equal(await text(".task-title"), surface === "dashboard" ? "A task 26" : "A task 1");
      if (surface === "dashboard") assert.equal(await groupLabels().then(groups => groups[0][0]), "甲项目");
      await evaluate('post({method:"ui/notifications/host-context-changed",params:{locale:"en"}})');
      await wait(async () => (await text("#refresh")).trim() === "Refresh", "host locale update");
      assert.equal(await inside(query("#previous-page") + ".getAttribute('aria-label')"), "Previous page");
      await click("#add-task");
      assert.equal(await text("#dialog-heading"), "Add task");
      await click("#dialog-close");
      await evaluate('projects[0].tasks.find(task => task.id === (dashboard ? "A26" : "A1")).title = "任务原文";');
      await click("#refresh");
      await wait(async () => (await text("#refresh-status")) === "Tasks refreshed.", "English refresh feedback");
      assert.ok((await inside("[...document.querySelectorAll('.task-title')].map(node => node.textContent)")).includes("任务原文"));
    }
  }
  assert.deepEqual(exceptions, []);
  console.log("PASS Dashboard 与项目侧栏 Chromium 分页、优先级菜单、组合筛选、新建任务聚焦、中英文及语言回退。");
} finally {
  clearTimeout(timeout);
  browser.kill();
  await browserExited;
  rmSync(directory, { recursive: true, force: true });
}
