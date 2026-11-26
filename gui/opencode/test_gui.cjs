const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { test } = require("node:test");

function element(tag) {
  const node = {
    tag, style: {}, dataset: {}, children: [],
    setAttribute() {}, addEventListener() {},
    append(...children) { this.children.push(...children); for (const child of children) child.parent = this; },
    remove() { this.parent.children = this.parent.children.filter((child) => child !== this); },
  };
  if (tag === "iframe") node.contentWindow = { messages: [], postMessage(message) { this.messages.push(message); } };
  return node;
}

function setup() {
  let session;
  let updateSession;
  let cleanup;
  let insideEffect = false;
  const listeners = new Set();
  const registered = new Map();
  const sdk = {
    Command: "command", MenuItem: "menu", Panel: "panel", TitlebarItem: "titlebar",
    Ipc: { define: (value) => value }, Plugin: { define: (value) => value },
  };
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(`${__dirname}/gui-experimental.cjs`, "utf8"), {
    require: (id) => {
      if (id === "@opencode/plugin/gui") return sdk;
      if (id === "effect") return { Schema: {} };
      if (id !== "solid-js") return require(id);
      return {
        createEffect: (run) => {
          if (insideEffect) return run();
          updateSession = () => {
            cleanup?.();
            cleanup = undefined;
            insideEffect = true;
            try { run(); } finally { insideEffect = false; }
          };
          updateSession();
        },
        onCleanup: (dispose) => { cleanup = dispose; },
      };
    },
    module, navigator: { languages: ["en-US"] }, document: { createElement: element },
    window: {
      addEventListener: (_, listener) => listeners.add(listener),
      removeEventListener: (_, listener) => listeners.delete(listener),
    },
  });
  let dashboard;
  const calls = [];
  const bridge = {
    load: async () => JSON.stringify({ html: "<head></head>", config: { version: 1 } }),
    call: (payload) => new Promise((resolve) => calls.push({
      ...JSON.parse(payload), settle: (data = {}) => resolve(JSON.stringify({ content: [], structuredContent: data })),
    })),
  };
  module.exports.default.setup({
    id: "worklog-opencode-gui", app: { platform: "desktop", version: "2.4.1" },
    main: () => bridge, add: (registry, definition) => registered.set(registry, definition),
    t: (key) => key, appearance: { mode: () => "dark" }, locale: { locale: () => "zh-Hans" },
    dialogs: { open: (render) => { dashboard = render({ close: () => {} }); } },
    sessions: { current: () => session },
  });
  return {
    calls, registered, get session() { return session; },
    get dashboard() { return dashboard; },
    change: (next) => { session = next; updateSession?.(); },
    setSession: (next) => { session = next; },
    send: (frame, message, origin = "null") => {
      for (const listener of listeners) listener({
        source: frame.contentWindow, origin, data: { jsonrpc: "2.0", ...message },
      });
    },
  };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
const initialize = { id: 1, method: "ui/initialize" };
const taskWrite = (id, operation) => ({
  id, method: "tools/call", params: {
    name: "mutate_worklog_task", arguments: { command: "task-set-priority", request: { operation_id: operation } },
  },
});
const fixture = (suffix) => ({ id: `session-${suffix}`, directory: `/projects/${suffix}`, server: { builtin: true } });

test("Dashboard 启动无需聊天，向 iframe 传递会话无关的全局工具调用", async () => {
  const host = setup();
  assert.equal(host.session, undefined);
  host.registered.get("titlebar")().run();
  await tick();
  const frame = host.dashboard.children[1].children[1];
  host.send(frame, initialize);
  host.send(frame, { method: "ui/notifications/initialized" });
  assert.equal(frame.contentWindow.messages[0].result.hostContext.locale, "zh-Hans");
  assert.equal(host.calls[0].name, "open_worklog_dashboard");
  assert.equal(host.calls[0].directory, null);
  assert.equal(host.calls[0].sessionId, null);
  host.calls[0].settle({ state: "ready" });
  await tick();
  assert.equal(frame.contentWindow.messages[1].method, "ui/notifications/tool-result");
});

test("项目操作在提交时绑定，旧面板拒绝新请求且结果不会传给新面板", async () => {
  const host = setup();
  host.change(fixture("a"));
  const view = host.registered.get("panel").render({ get session() { return host.session; } });
  await tick();
  const frameA = view.children[1];
  host.send(frameA, initialize, "https://unexpected.test");
  assert.equal(frameA.contentWindow.messages.length, 0);
  host.send(frameA, initialize);
  host.send(frameA, taskWrite(2, "a"));
  assert.equal(host.calls[0].directory, "/projects/a");
  assert.equal(host.calls[0].sessionId, "session-a");
  host.setSession(fixture("b"));
  host.send(frameA, taskWrite(3, "too-late"));
  assert.equal(frameA.contentWindow.messages[1].result.structuredContent.code, "stale_view");
  host.change(host.session);
  await tick();
  const frameB = view.children[1];
  host.send(frameB, initialize);
  host.send(frameB, taskWrite(2, "b"));
  assert.equal(host.calls[1].directory, "/projects/b");
  assert.equal(host.calls[1].sessionId, "session-b");
  host.calls[0].settle({ project: "a" });
  await tick();
  assert.equal(frameB.contentWindow.messages.length, 1);
  host.calls[1].settle({ project: "b" });
  await tick();
  assert.equal(frameB.contentWindow.messages[1].result.structuredContent.project, "b");
});
