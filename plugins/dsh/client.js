window.__ModuleLoader__.load({
  id: "worklog-dsh",
  factory(require) {
    const { createElement: h, useEffect, useRef, useState } = require("react");
    const id = "worklog-dsh";
    const title = "workLog Dashboard";
    const sidebarTitle = "workLog 项目侧栏";
    const api = "api/worklog-dsh";
    const releaseUrl = /^https:\/\/github\.com\/(?:floohubAI|LaudasMe)\/worklog\/releases\/tag\/[A-Za-z0-9.-]+$/i;
    const frameStyle = { border: 0, width: "100%", height: "100%", display: "block" };

    async function send(leaf, data, signal) {
      const response = await fetch(api + leaf, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify(data), signal, cache: "no-store",
      });
      return response.json();
    }

    function hostContext(config) {
      return {
        client: "dsh",
        locale: document.documentElement.lang || undefined,
        systemLocale: navigator.languages?.[0],
        theme: document.body.hasAttribute("data-ds-dark-theme") ? "dark" : "light",
        config,
        supportsContinue: false,
        updateComponents: ["cli"],
      };
    }

    function unavailable(code, message) {
      return { content: [], structuredContent: { state: "unavailable", code, message } };
    }

    function staleView() {
      return unavailable("stale_view", "会话已切换，请重新打开 workLog 面板。");
    }

    function Icon({ size = 20 }) {
      return h("svg", { "aria-hidden": true, width: size, height: size, viewBox: "0 0 20 20" },
        h("path", { d: "M4 4h12v12H4z M7 8h6 M7 12h6", fill: "none", stroke: "currentColor", strokeWidth: 1.5 }));
    }

    function SharedPanel({ surface, sessionId, isCurrent }) {
      const frame = useRef(null);
      const [assets, setAssets] = useState(null);
      const [error, setError] = useState("");
      useEffect(() => {
        const controller = new AbortController();
        send("/assets", { surface, sessionId }, controller.signal).then(result => {
          if (controller.signal.aborted) return;
          if (typeof result?.html === "string" && result.config && result.binding) setAssets(result);
          else setError(result?.message || "无法加载 workLog 页面。");
        }).catch(() => {
          if (!controller.signal.aborted) setError("无法连接 workLog 插件，请重新打开面板。");
        });
        return () => controller.abort();
      }, [surface, sessionId]);

      useEffect(() => {
        if (!assets) return;
        let live = true;
        const currentWindow = () => frame.current?.contentWindow;
        const post = (target, message) => {
          if (live && target === currentWindow()) target.postMessage({ jsonrpc: "2.0", ...message }, "*");
        };
        const openTool = surface === "dashboard" ? "open_worklog_dashboard" : "open_worklog_codex_panel";

        async function tool(name, args) {
          if (!isCurrent()) return staleView();
          try {
            // 读取及写入均使用当前 iframe 的绑定。会话切换后，已提交的请求可以保存到原项目。
            return await send("/tool", { binding: assets.binding, name, arguments: args });
          } catch {
            return unavailable("bridge_unavailable", "无法读取 workLog 的回执，请重试。");
          }
        }

        async function onMessage(event) {
          const target = currentWindow();
          const message = event.data;
          if (!target || event.source !== target || event.origin !== "null" || message?.jsonrpc !== "2.0") return;

          if (message.method === "ui/notifications/initialized") {
            const result = await tool(openTool, {});
            if (isCurrent()) post(target, { method: "ui/notifications/tool-result", params: result });
            return;
          }
          if (!Number.isSafeInteger(message.id)) return;
          const reply = result => post(target, { id: message.id, result });
          if (message.method === "ui/initialize") {
            reply({
              protocolVersion: "2026-01-26", hostInfo: { name: "DeepSeek Harness", version: "0.2.2-rc.5" },
              hostCapabilities: { openLinks: {} }, hostContext: hostContext(assets.config),
            });
          } else if (message.method === "tools/call") {
            const admitted = isCurrent();
            const result = await tool(message.params?.name, message.params?.arguments ?? {});
            // 送回尚未提交请求的失效回执。已提交的旧请求由原项目读回，当前页面忽略其回执。
            if (!admitted || isCurrent()) reply(result);
          } else if (message.method === "ui/open-link" && isCurrent()) {
            const url = message.params?.url;
            if (typeof url === "string" && releaseUrl.test(url)) {
              window.open(url, "_blank", "noopener,noreferrer");
              reply({});
            } else {
              reply({ isError: true });
            }
          } else {
            post(target, { id: message.id, error: { code: -32601, message: "Method not available." } });
          }
        }

        const observer = new MutationObserver(() => {
          const target = currentWindow();
          if (target && isCurrent()) post(target, {
            method: "ui/notifications/host-context-changed", params: hostContext(assets.config),
          });
        });
        observer.observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
        observer.observe(document.body, { attributes: true, attributeFilter: ["data-ds-dark-theme"] });
        window.addEventListener("message", onMessage);
        return () => {
          live = false;
          observer.disconnect();
          window.removeEventListener("message", onMessage);
        };
      }, [assets, surface, isCurrent]);

      if (!assets) return h("p", { role: error ? "alert" : "status", style: { padding: 16 } },
        error || "正在加载 workLog…");
      return h("iframe", {
        ref: frame, title: surface === "dashboard" ? title : sidebarTitle,
        sandbox: "allow-scripts", srcDoc: assets.html, style: frameStyle,
      });
    }

    function apply(ctx) {
      function Dashboard() {
        return h("section", {
          style: { height: "100%", paddingTop: "var(--dsh-frame-top-clearance, 0px)", boxSizing: "border-box" },
        }, h(SharedPanel, { surface: "dashboard", sessionId: null, isCurrent: () => true }));
      }
      function ProjectPane(props) {
        const { tab } = props.useTabInfo();
        const sessionId = props.sessionId;
        if (!tab.visible) return null;
        return h(SharedPanel, {
          key: sessionId, surface: "project", sessionId,
          isCurrent: () => !tab.signal.aborted && ctx.sidebarRight.mounted.getSnapshot() === sessionId,
        });
      }
      ctx.effect(() => ctx.slots.inject("main",
        () => ctx.slots.register({ name: "main", key: id }, Dashboard)));
      ctx.effect(() => ctx.slots.inject("sidebar.panellist",
        () => ctx.slots.register({ name: "sidebar.panellist", id, order: 120, label: title }, Icon)));
      ctx.effect(() => ctx.sidebarRightTabs.register({
        id, kind: id, title: () => sidebarTitle,
        guide: [{ id, order: 120, title: () => sidebarTitle, icon: Icon }],
      }));
      ctx.effect(() => ctx.slots.inject("sidebar.right.pane.tab",
        () => ctx.slots.register({ name: "sidebar.right.pane.tab", key: id }, ProjectPane)));
    }

    return { inject: ["slots", "sidebarRightTabs", "sidebarRight"], apply };
  },
});
