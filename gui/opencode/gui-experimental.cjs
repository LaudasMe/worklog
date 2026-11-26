const { Command, Ipc, MenuItem, Panel, Plugin, TitlebarItem } = require("@opencode/plugin/gui");
const { Schema } = require("effect");
const { createEffect, onCleanup } = require("solid-js");
const { execFile } = require("node:child_process");
const { realpath } = require("node:fs/promises");
const { homedir } = require("node:os");
const { isAbsolute, join } = require("node:path");

const tools = new Set([
  "read_worklog_codex_handoff", "read_worklog_task_sections", "read_worklog_history",
  "read_worklog_completion_options", "read_worklog_blocker_options", "mutate_worklog_task",
  "check_worklog_update",
]);
const entryTool = { dashboard: "open_worklog_dashboard", project: "open_worklog_codex_panel" };
const copy = {
  en: {
    dashboard: "workLog Dashboard", project: "workLog Project", close: "Close",
    loading: "Loading workLog…", local: "Open an OpenCode session in a local project.",
    install: "Install or update the workLog Skill, then reopen this panel.",
    stale: "This panel belongs to a different session. Reopen the panel to continue.",
    failure: "OpenCode could not complete the workLog request. Try again.",
  },
  zh: {
    dashboard: "workLog Dashboard", project: "workLog 项目侧栏", close: "关闭",
    loading: "正在加载 workLog…", local: "请在本机项目目录打开 OpenCode 会话。",
    install: "请安装或更新 workLog Skill，再重新打开面板。",
    stale: "此面板已关联另一条会话，请重新打开面板。",
    failure: "OpenCode 未能完成 workLog 请求，请重试。",
  },
};

function contract(ctx) {
  return Ipc.define({
    id: `${ctx.id}.worklog`,
    methods: {
      load: { input: Schema.String, output: Schema.String },
      call: { input: Schema.String, output: Schema.String },
    },
  });
}

function python(script, args, request) {
  return new Promise((resolve, reject) => {
    const child = execFile("python3", ["-B", script, ...args], {
      timeout: 30000,
      maxBuffer: 16 * 1024 * 1024,
    }, (error, stdout) => {
      try {
        resolve(JSON.parse(stdout));
      } catch {
        reject(error || new Error("Invalid JSON from workLog"));
      }
    });
    child.stdin.on("error", () => {});
    child.stdin.end(request === undefined ? undefined : JSON.stringify(request));
  });
}

function allowedTool(surface, name, args) {
  return name === entryTool[surface] || (tools.has(name)
    && (name !== "check_worklog_update" || args.component === "cli"));
}

function resultError(code, message, retryable = false) {
  return { content: [], structuredContent: { state: "unavailable", code, message, retryable } };
}

function main(ctx) {
  const skill = join(homedir(), ".agents", "skills", "worklog");
  const script = (name) => join(skill, "scripts", `${name}.py`);
  ctx.provide(contract(ctx), {
    load: async (surface) => {
      if (!Object.hasOwn(entryTool, surface)) throw new Error("Unknown workLog surface");
      try {
        return JSON.stringify(await python(script("panel_assets"), ["--skill", skill, "--surface", surface]));
      } catch {
        return JSON.stringify({ code: "shared_resources_unavailable" });
      }
    },
    call: async (payload) => {
      const { surface, sessionId, directory, name, arguments: args } = JSON.parse(payload);
      if (!Object.hasOwn(entryTool, surface) || !args || Array.isArray(args) || typeof args !== "object"
        || !allowedTool(surface, name, args)) {
        throw new Error("Unsupported workLog operation");
      }
      const request = { client: "opencode", surface, sessionId: null, directory: null, name, arguments: args };
      if (surface === "project") {
        if (typeof sessionId !== "string" || !sessionId || typeof directory !== "string" || !isAbsolute(directory)) {
          throw new Error("Invalid OpenCode session");
        }
        request.sessionId = sessionId;
        try {
          request.directory = await realpath(directory);
        } catch {
          return JSON.stringify(resultError("invalid_project_path", "Current project directory is unavailable."));
        }
      }
      return JSON.stringify(await python(script("panel_bridge"), [], request));
    },
  });
}

function panelView(ctx, bridge, surface, currentSession) {
  const container = document.createElement("div");
  container.style.cssText = "height:100%;min-height:0;width:100%;position:relative";
  const status = document.createElement("div");
  status.setAttribute("role", "status");
  status.style.cssText = "padding:1rem";
  container.append(status);
  const context = (config) => ({
    client: "opencode", theme: ctx.appearance.mode(), locale: ctx.locale.locale(),
    systemLocale: navigator.languages[0] || navigator.language,
    config, supportsContinue: false, updateComponents: ["cli"],
  });
  createEffect(() => {
    const session = currentSession?.();
    const label = () => ctx.t(surface);
    status.hidden = false;
    status.textContent = ctx.t("loading");
    // On project switches OpenCode reuses the component. Each frame owns one immutable visit.
    if (surface === "project" && (!session?.server?.builtin || session.pending || !session.directory)) {
      status.textContent = ctx.t("local");
      return;
    }
    let frame;
    let disposed = false;
    let initialized = false;
    let config;
    const toFrame = (message) => {
      if (!disposed) frame?.contentWindow?.postMessage({ jsonrpc: "2.0", ...message }, "*");
    };
    const call = (name, args) => bridge.call(JSON.stringify({
      surface, sessionId: session?.id ?? null, directory: session?.directory ?? null,
      name, arguments: args,
    })).then(JSON.parse).catch(() => resultError("host_unavailable", ctx.t("failure"), true));
    const stale = () => surface === "project" && currentSession() !== session;
    const receive = (event) => {
      const message = event.data;
      if (disposed || !frame || event.source !== frame.contentWindow || event.origin !== "null"
        || message?.jsonrpc !== "2.0") return;
      const { id, method, params } = message;
      if (method === "ui/notifications/initialized" && initialized && !stale()) {
        call(entryTool[surface], {}).then((result) => toFrame({ method: "ui/notifications/tool-result", params: result }));
        return;
      }
      if (!Number.isSafeInteger(id)) return;
      if (method === "ui/initialize" && !initialized) {
        initialized = true;
        toFrame({ id, result: {
          protocolVersion: "2026-01-26", hostInfo: { name: "OpenCode", version: ctx.app.version },
          hostCapabilities: { serverTools: {}, openLinks: {} }, hostContext: context(config),
        } });
        return;
      }
      if (!initialized) return;
      if (method === "ui/open-link" && !stale() && typeof params?.url === "string"
        && /^https:\/\/github\.com\/(?:LaudasMe|floohubAI)\/worklog\/releases\/tag\/[A-Za-z0-9.-]+$/i.test(params.url)) {
        ctx.system.openExternal(params.url);
        toFrame({ id, result: {} });
        return;
      }
      const name = params?.name;
      const args = params?.arguments ?? {};
      if (method !== "tools/call" || typeof args !== "object" || Array.isArray(args) || !allowedTool(surface, name, args)) {
        toFrame({ id, error: { code: -32602, message: ctx.t("failure") } });
        return;
      }
      if (stale()) {
        toFrame({ id, result: resultError("stale_view", ctx.t("stale")) });
        return;
      }
      // Do not cancel an accepted operation or derive its target again after switching sessions.
      call(name, args).then((result) => toFrame({ id, result }));
    };
    window.addEventListener("message", receive);
    onCleanup(() => {
      disposed = true;
      window.removeEventListener("message", receive);
      frame?.remove();
    });
    bridge.load(surface).then((payload) => {
      if (disposed) return;
      const asset = JSON.parse(payload);
      if (!asset.html || !asset.config) {
        status.textContent = ctx.t("install");
        status.dataset.error = asset.code || "shared_resources_unavailable";
        return;
      }
      config = asset.config;
      frame = document.createElement("iframe");
      frame.title = label();
      frame.setAttribute("sandbox", "allow-scripts");
      const policy = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; base-uri 'none'";
      frame.srcdoc = asset.html.replace(/<head>/i, `<head><meta http-equiv="Content-Security-Policy" content="${policy}">`);
      frame.style.cssText = "display:block;height:100%;width:100%;border:0";
      container.append(frame);
      status.hidden = true;
    }).catch(() => {
      if (!disposed) status.textContent = ctx.t("install");
    });
    createEffect(() => {
      const hostContext = context(config);
      if (initialized) toFrame({ method: "ui/notifications/host-context-changed", params: hostContext });
    });
  });
  return container;
}

function setup(ctx) {
  if (ctx.app.platform !== "desktop") return;
  const bridge = ctx.main(contract(ctx));
  const projectPanel = `${ctx.id}:project`;
  function openDashboard() {
    ctx.dialogs.open((dialog) => {
      const container = document.createElement("section");
      container.setAttribute("aria-label", ctx.t("dashboard"));
      // OpenCode's dialog layer is click-through until its content opts in to pointer events.
      container.style.cssText = "width:min(76rem,94vw);height:min(52rem,84vh);display:flex;flex-direction:column;background:var(--background-base);border-radius:.75rem;overflow:hidden;pointer-events:auto";
      const close = document.createElement("button");
      close.type = "button";
      close.textContent = ctx.t("close");
      close.style.cssText = "align-self:end;padding:.6rem 1rem;cursor:pointer";
      close.addEventListener("click", () => dialog.close());
      const view = panelView(ctx, bridge, "dashboard");
      view.style.flex = "1";
      container.append(close, view);
      return container;
    });
  }
  ctx.add(TitlebarItem, () => ({ id: "dashboard", label: ctx.t("dashboard"), icon: "file-tree", run: openDashboard }));
  ctx.add(Command, () => ({ id: "dashboard", title: ctx.t("dashboard"), run: openDashboard }));
  const tab = { id: "project", get title() { return ctx.t("project"); }, closable: "compact" };
  ctx.add(Panel, {
    id: "project", region: "side", width: 420,
    list: (input) => input.open.includes(tab.id) ? [tab] : [],
    render: (props) => panelView(ctx, bridge, "project", () => props.session),
  });
  ctx.add(MenuItem, () => {
    const session = ctx.sessions.current();
    if (!session?.server?.builtin || session.pending) return undefined;
    return {
      id: "project", menu: "session.panel", title: ctx.t("project"), icon: "file-tree",
      opened: ctx.layout.state(projectPanel, session) !== "closed",
      run: () => ctx.layout.open(projectPanel, session),
    };
  });
}

module.exports = { default: Plugin.define({ i18n: copy, main, setup }) };
