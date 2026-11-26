import assert from "node:assert/strict";
import { copyFile, mkdir, mkdtemp, readFile, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { pathToFileURL } from "node:url";
import { runInNewContext } from "node:vm";

test("DSH 宿主注册包内 Skill，并冻结会话的项目目录", async t => {
  const temporary = await mkdtemp(join(tmpdir(), "worklog-dsh-test-"));
  t.after(() => rm(temporary, { recursive: true, force: true }));
  const skill = join(temporary, "skills", "worklog");
  await mkdir(join(skill, "scripts"), { recursive: true });
  await copyFile(new URL("./host.mjs", import.meta.url), join(temporary, "host.mjs"));
  await writeFile(join(skill, "SKILL.md"), "---\nname: worklog\ndescription: Synthetic test.\n---\n\nWorklog.\n");
  await writeFile(join(skill, "scripts", "panel_assets.py"), `
import json, sys
assert sys.argv[1] == "--skill"
assert sys.argv[3] == "--surface"
print(json.dumps({"html": sys.argv[4], "config": {"version": 1}}))
`);
  await writeFile(join(skill, "scripts", "panel_bridge.py"), `
import json, sys
request = json.load(sys.stdin)
receipt = {"state": "ready", "payload": request}
print(json.dumps({"content": [], "structuredContent": receipt}))
`);
  await mkdir(join(temporary, "A"));
  await mkdir(join(temporary, "B"));
  const routes = new Map();
  const sessions = new Map([["a", join(temporary, "A")], ["b", join(temporary, "B")]]);
  const { apply } = await import(pathToFileURL(join(temporary, "host.mjs")));
  let registeredSkill;
  await apply({
    skills: { register(entry) { registeredSkill = entry; } },
    sessions: { get(id) { return sessions.has(id) ? { id } : undefined; } },
    workingDirectory: { get(session) { return sessions.get(session.id); } },
    connection: { fetch: { register(route) { routes.set(route.path, route); } } },
  });
  assert.equal(registeredSkill.name, "worklog");
  assert.match(registeredSkill.path, /\/skills\/worklog\/SKILL\.md$/);
  assert.deepEqual(registeredSkill.resourceBase, { kind: "directory", path: await realpath(skill) });

  async function call(leaf, data) {
    const path = "/api/worklog-dsh/" + leaf;
    const response = await routes.get(path).fetch(new Request("https://test.invalid" + path, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(data),
    }));
    return response.json();
  }

  const a = await call("assets", { surface: "project", sessionId: "a" });
  const b = await call("assets", { surface: "project", sessionId: "b" });
  const dashboard = await call("assets", { surface: "dashboard", sessionId: null });
  assert.equal(a.html, "project");
  assert.equal(dashboard.html, "dashboard");
  assert.equal(dashboard.binding.directory, null);
  assert.match(a.binding.directory, /\/A$/);
  assert.match(b.binding.directory, /\/B$/);
  assert.equal((await call("assets", { surface: "project", sessionId: "unknown" })).code, "stale_view");

  const request = { name: "mutate_worklog_task", arguments: { command: "task-set-priority",
    request: { expected_revision: 1, task_id: "uuid-A" } } };
  const saved = await call("tool", { ...request, binding: a.binding });
  assert.deepEqual(saved.structuredContent.payload, { client: "dsh", ...a.binding, ...request });
  const unsafeTool = await call("tool", { ...request, binding: a.binding, name: "shell" });
  assert.equal(unsafeTool.structuredContent.code, "invalid_input");

  sessions.set("a", sessions.get("b"));
  const moved = await call("tool", { ...request, binding: a.binding });
  assert.equal(moved.structuredContent.code, "stale_view");
  const readB = await call("tool", { name: "open_worklog_codex_panel", arguments: {}, binding: b.binding });
  assert.equal(readB.structuredContent.payload.directory, b.binding.directory);
});

test("DSH 会话切换不会转移已提交的请求或回执", async () => {
  const client = await readFile(new URL("./client.js", import.meta.url), "utf8");
  const listeners = new Set();
  const registeredSlots = new Map();
  const submitted = [];
  let render;
  let plugin;
  let activeSession = "a";
  const react = {
    createElement: (component, props) => ({ component, props }),
    useRef() { return { current: { contentWindow: render.frame } }; },
    useState(initial) { return [initial === null ? render.assets : initial, () => {}]; },
    useEffect(effect) { render.effects.push(effect); },
  };
  runInNewContext(client, {
    window: {
      __ModuleLoader__: { load({ factory }) { plugin = factory(() => react); } },
      addEventListener(_, handler) { listeners.add(handler); },
      removeEventListener(_, handler) { listeners.delete(handler); },
    },
    document: { documentElement: {}, body: {} },
    MutationObserver: class { observe() {} disconnect() {} },
    AbortController,
    fetch(path, options) {
      if (path.endsWith("/assets")) return Promise.resolve({ json: async () => ({}) });
      return new Promise(resolve => {
        submitted.push({
          payload: JSON.parse(options.body),
          deliver: receipt => resolve({ json: async () => receipt }),
        });
      });
    },
  });
  plugin.apply({
    effect: setup => setup(),
    slots: {
      inject: (_, setup) => setup(),
      register({ name }, component) { registeredSlots.set(name, component); },
    },
    sidebarRightTabs: { register(definition) { assert.equal(definition.guide[0].id, "worklog-dsh"); } },
    sidebarRight: { mounted: { getSnapshot: () => activeSession } },
  });
  assert.deepEqual([...registeredSlots.keys()].sort(), ["main", "sidebar.panellist", "sidebar.right.pane.tab"]);

  function mount(sessionId) {
    const replies = [];
    const frame = { postMessage: message => replies.push(message) };
    const assets = { html: "test", config: { version: 1 },
      binding: { surface: "project", sessionId, directory: "/synthetic/" + sessionId } };
    render = { effects: [], assets, frame };
    const { component, props } = registeredSlots.get("sidebar.right.pane.tab")({
      sessionId, useTabInfo: () => ({ tab: { visible: true, signal: new AbortController().signal } }),
    });
    component(props);
    const cleanup = render.effects.map(effect => effect()).filter(Boolean);
    return { frame, replies, cleanup: () => cleanup.forEach(dispose => dispose()) };
  }

  function dispatch(source, id, args) {
    return Promise.all([...listeners].map(handler => handler({
      source, origin: "null",
      data: { jsonrpc: "2.0", id, method: "tools/call", params: { name: "mutate_worklog_task", arguments: args } },
    })));
  }

  const a = mount("a");
  const args = { command: "task-set-priority", request: { task_id: "uuid-A", expected_revision: 2 } };
  await dispatch({}, 10, args);
  assert.equal(submitted.length, 0);
  const aWrite = dispatch(a.frame, 11, args);
  assert.equal(submitted[0].payload.binding.sessionId, "a");
  assert.equal(submitted[0].payload.arguments.request.expected_revision, 2);

  activeSession = "b";
  const b = mount("b");
  await dispatch(a.frame, 12, args);
  assert.equal(submitted.length, 1);
  assert.equal(a.replies[0].result.structuredContent.code, "stale_view");

  const bWrite = dispatch(b.frame, 11, { ...args, request: { task_id: "uuid-B", expected_revision: 1 } });
  assert.equal(submitted[1].payload.binding.sessionId, "b");
  submitted[0].deliver({ content: [], structuredContent: { state: "ready", task: { id: "uuid-A" } } });
  submitted[1].deliver({ content: [], structuredContent: { state: "ready", task: { id: "uuid-B" } } });
  await Promise.all([aWrite, bWrite]);
  assert.equal(a.replies.length, 1);
  assert.equal(b.replies.length, 1);
  assert.equal(b.replies[0].id, 11);
  assert.equal(b.replies[0].result.structuredContent.task.id, "uuid-B");
  a.cleanup();
  b.cleanup();
});
