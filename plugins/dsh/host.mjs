import { spawn } from "node:child_process";
import { readFile, realpath } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

export const name = "worklog-dsh";
export const inject = ["connection", "sessions", "workingDirectory", "skills"];

const skillRoot = fileURLToPath(new URL("./skills/worklog", import.meta.url));
const api = "/api/worklog-dsh";
const toolNames = new Set([
  "open_worklog_dashboard", "open_worklog_codex_panel", "read_worklog_codex_handoff",
  "read_worklog_task_sections", "read_worklog_history", "check_worklog_update",
  "read_worklog_completion_options", "read_worklog_blocker_options", "mutate_worklog_task",
]);
const assetErrors = new Set(["shared_resources_unavailable", "shared_resources_incompatible"]);
const maxOutput = 16 * 1024 * 1024;
const maxRequest = 256 * 1024;
const unavailable = (code, message) => ({ state: "unavailable", code, message });
const stale = () => unavailable("stale_view", "会话或工作目录已变化，请重新打开 workLog 面板。");
const invalid = () => unavailable("invalid_input", "workLog 面板请求无效。");

function runPython(script, args, payload) {
  return new Promise((resolve, reject) => {
    const child = spawn("python3", ["-B", join(skillRoot, "scripts", script), ...args], { timeout: 30_000 });
    const chunks = [];
    let size = 0;
    child.stdout.on("data", chunk => {
      size += chunk.length;
      if (size > maxOutput) child.kill();
      else chunks.push(chunk);
    });
    child.stderr.resume();
    child.on("error", reject);
    child.stdin.on("error", reject);
    child.on("close", code => {
      if (size > maxOutput || code === null) {
        reject(new Error("workLog Python bridge stopped or exceeded its output limit."));
        return;
      }
      try {
        const result = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        resolve({ code, result });
      } catch (error) {
        reject(error);
      }
    });
    child.stdin.end(payload === undefined ? undefined : JSON.stringify(payload));
  });
}

async function readJson(request) {
  if (request.headers.get("content-type")?.split(";", 1)[0] !== "application/json" || !request.body) {
    return undefined;
  }
  let size = 0;
  const chunks = [];
  for await (const chunk of request.body) {
    size += chunk.length;
    if (size > maxRequest) return undefined;
    chunks.push(chunk);
  }
  try {
    const value = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    return value && typeof value === "object" && !Array.isArray(value) ? value : undefined;
  } catch {
    return undefined;
  }
}

export async function apply(ctx) {
  const skillPath = await realpath(join(skillRoot, "SKILL.md"));
  const source = await readFile(skillPath, "utf8");
  const frontmatter = /^---\r?\n([\s\S]+?)\r?\n---\r?\n/.exec(source);
  const description = frontmatter?.[1].match(/^description: (.+)$/m)?.[1].trim();
  if (!description) throw new Error("workLog 插件内的 SKILL.md 缺少描述。请重新安装插件。");
  ctx.skills.register({
    name: "worklog", description, source: "bundled", path: skillPath,
    resourceBase: { kind: "directory", path: skillRoot }, content: source.slice(frontmatter[0].length).trim(),
  });

  async function binding(surface, sessionId) {
    if (surface === "dashboard" && sessionId === null) return { surface, sessionId, directory: null };
    if (surface !== "project" || typeof sessionId !== "string" || !sessionId || sessionId.length > 128) {
      return undefined;
    }
    const session = ctx.sessions.get(sessionId);
    if (!session) return undefined;
    const workingDirectory = ctx.workingDirectory.get(session);
    try {
      return { surface, sessionId, directory: await realpath(workingDirectory) };
    } catch {
      return undefined;
    }
  }

  function route(leaf, action, fail) {
    ctx.connection.fetch.register({
      path: api + leaf,
      methods: ["POST"],
      requestBody: "streaming",
      async fetch(request) {
        try {
          const response = await action(await readJson(request));
          return Response.json(response, { headers: { "cache-control": "no-store" } });
        } catch {
          return Response.json(fail, { status: 500, headers: { "cache-control": "no-store" } });
        }
      },
    });
  }

  route("/assets", async body => {
    if (!body) return invalid();
    const target = await binding(body.surface, body.sessionId);
    if (!target) return stale();
    const { code, result } = await runPython("panel_assets.py", ["--skill", skillRoot, "--surface", target.surface]);
    if (code !== 0 || typeof result?.html !== "string" || !result.config) {
      return unavailable(
        assetErrors.has(result?.code) ? result.code : "shared_resources_unavailable",
        "无法加载兼容的 workLog 页面，请重新安装 workLog 插件。",
      );
    }
    return { ...result, binding: target };
  }, unavailable("shared_resources_unavailable", "无法加载 workLog 页面，请检查 Python 3 并重新安装插件。"));

  route("/tool", async body => {
    if (!body || !toolNames.has(body.name) || !body.arguments || typeof body.arguments !== "object"
        || Array.isArray(body.arguments)) return { content: [], structuredContent: invalid() };
    const target = await binding(body.binding?.surface, body.binding?.sessionId);
    if (!target || target.directory !== body.binding.directory) {
      return { content: [], structuredContent: stale() };
    }
    const { result } = await runPython("panel_bridge.py", [], {
      client: "dsh", ...target, name: body.name, arguments: body.arguments,
    });
    return result;
  }, { content: [], structuredContent: unavailable("bridge_unavailable", "无法调用 workLog，请检查 Python 3 后重试。") });
}
