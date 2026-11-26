const { Ipc, Plugin } = require("@opencode/plugin/gui");
const { Schema } = require("effect");
const { appendFileSync, readFileSync } = require("node:fs");
const { createHash, randomUUID } = require("node:crypto");
const { join } = require("node:path");
const { execFileSync, spawn } = require("node:child_process");
const token = (ctx) => Ipc.define({ id: `${ctx.id}.probe`, methods: {
  event: { input: Schema.String }, fixtures: { output: Schema.String },
  evaluate: { input: Schema.String, output: Schema.String },
  pointer: { input: Schema.String },
  task: { input: Schema.String, output: Schema.String },
  lock: {}, unlock: {},
  asset: { input: Schema.String, output: Schema.String },
} });
function main(ctx) {
  const root = ctx.options.workspace;
  if (typeof root !== "string" || process.env.OPENCODE_DESKTOP_TEST_ROOT !== join(root, "desktop-profile")) {
    throw new Error("Test instance required");
  }
  // OpenCode Desktop requires the original HOME during startup. Only the isolated main process adopts the fixture HOME.
  process.env.HOME = join(root, "home");
  const skill = join(process.env.HOME, ".agents/skills/worklog");
  const cli = join(skill, "scripts/worklog.py");
  const record = (event) => appendFileSync(join(root, "probe.jsonl"), event + "\n");
  const fixtures = JSON.parse(readFileSync(join(root,"fixtures.json"),"utf8"));
  let lock;
  ctx.provide(token(ctx), {
    event: record,
    async fixtures() {
      const endpoint = ctx.serverEndpoints.get("sidecar");
      if (!endpoint?.sameMachine) throw new Error("Expected isolated local sidecar");
      for (const [index, item] of fixtures.entries()) {
        const result = await fetch(new URL("/api/session",endpoint.url),{
          method:"POST",headers:{...endpoint.headers,"content-type":"application/json"},
          body:JSON.stringify({location:{directory:item.directory},title:`Fixture ${index+1}`}),
        });
        if (!result.ok) throw Error("Synthetic session HTTP " + result.status);
        const {data}=await result.json();
        item.sessionId=data.id;
      }
      return JSON.stringify(fixtures);
    },
    async evaluate(payload, caller) {
      const { surface, script } = JSON.parse(payload);
      const frames = ctx.windows.get(caller.window)?.webContents.mainFrame.framesInSubtree || [];
      for (const frame of frames) {
        if (frame.url === "about:srcdoc"
          && await frame.executeJavaScript("document.documentElement.dataset.surface") === surface) {
          return JSON.stringify(await frame.executeJavaScript(script, true) ?? null);
        }
      }
      return "null";
    },
    async pointer(payload, caller) {
      const { selector, host } = JSON.parse(payload);
      const window = ctx.windows.get(caller.window);
      const web = window?.webContents;
      if (!web) throw new Error("验收窗口不可用");
      let target = web.mainFrame;
      if (!host) {
        for (const item of web.mainFrame.framesInSubtree) {
          if (item.url === "about:srcdoc"
            && await item.executeJavaScript("document.documentElement.dataset.surface") === "dashboard") {
            target = item;
            break;
          }
        }
      }
      window.focus();
      const inner = await target.executeJavaScript(`(() => {
        const element = document.querySelector(${JSON.stringify(selector)});
        if (!element) return null;
        element.scrollIntoView({ block: "nearest", inline: "nearest" });
        const box = element.getBoundingClientRect();
        return { x: box.left + box.width / 2, y: box.top + box.height / 2, width: innerWidth, height: innerHeight };
      })()`);
      if (!inner) throw new Error("找不到 Dashboard 鼠标测试目标");
      let point = { x: inner.x, y: inner.y };
      if (!host) {
        const outer = await web.mainFrame.executeJavaScript(`(() => {
          const frame = document.querySelector('iframe[title="workLog Dashboard"]');
          if (!frame) return null;
          const box = frame.getBoundingClientRect();
          return { left: box.left, top: box.top, width: box.width, height: box.height };
        })()`);
        if (!outer) throw new Error("Dashboard iframe 不可见");
        point = {
          x: outer.left + inner.x * outer.width / inner.width,
          y: outer.top + inner.y * outer.height / inner.height,
        };
      }
      const hit = await web.mainFrame.executeJavaScript(
        `document.elementFromPoint(${point.x}, ${point.y})?.tagName`,
      );
      record(JSON.stringify({ phase: "dashboard-pointer-target", selector, hit }));
      web.debugger.attach("1.3");
      try {
        await web.debugger.sendCommand("Input.dispatchMouseEvent", { type: "mouseMoved", ...point });
        await web.debugger.sendCommand("Input.dispatchMouseEvent", { type: "mousePressed", button: "left", buttons: 1, clickCount: 1, ...point });
        await web.debugger.sendCommand("Input.dispatchMouseEvent", { type: "mouseReleased", button: "left", buttons: 0, clickCount: 1, ...point });
      } finally {
        web.debugger.detach();
      }
    },
    lock() {
      return new Promise((resolve, reject) => {
        const file=join(fixtures[0].directory,".worklog/worklog.sqlite3");
        lock=spawn("python3",["-B","-u","-c", "import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); db.execute('BEGIN IMMEDIATE'); print('locked'); sys.stdin.readline(); db.rollback()",file]);
        lock.once("error",reject);
        lock.once("exit",(code)=>{if(code)reject(new Error(`SQLite guard exit ${code}`));});
        lock.stdout.once("data",()=>resolve());
      });
    },
    unlock() { lock?.stdin.end("release\n"); },
    asset(payload) {
      const { surface, html } = JSON.parse(payload);
      const rendered = JSON.parse(execFileSync("python3", ["-B", join(skill, "scripts/panel_assets.py"),
        "--skill", skill, "--surface", surface], { encoding: "utf8" }));
      if (html.replace(/<meta http-equiv="Content-Security-Policy"[^>]*>/i, "") !== rendered.html) {
        throw new Error("Frame differs from the installed shared page");
      }
      const sha = (path) => createHash("sha256").update(readFileSync(path)).digest("hex");
      const release = JSON.parse(execFileSync("python3", ["-B", cli, "release-version"], { encoding: "utf8" }));
      return JSON.stringify({ surface, skill, cli, release: release.result.release_version,
        html: sha(join(skill, "gui/panel.html")), config: sha(join(skill, "gui-resource.json")) });
    },
    task(payload) {
      const { index, number, priority } = JSON.parse(payload);
      const item = fixtures[index];
      const run = (command, args, input) => JSON.parse(execFileSync("python3",["-B",cli,"--project",item.directory,command,...args],{input,encoding:"utf8"}));
      const task = run("task-get",[number]).result;
      if (priority === undefined) return JSON.stringify(task);
      const input = JSON.stringify({ operation_id:randomUUID(),project_id:item.project_id,data_generation:item.data_generation,
        task_id:task.task_id,expected_revision:task.revision,priority });
      return JSON.stringify(run("task-set-priority",[],input).result.task);
    },
  });
  record(JSON.stringify({ phase:"main",version:ctx.app.version }));
}
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(fn,name) {
  for (let i=0;i<100;i++) { const value=await fn(); if(value) return value; await pause(100); }
  throw new Error(`Timeout: ${name}`);
}
function setup(ctx) {
  const probe=ctx.main(token(ctx));
  const observe=(phase,detail={})=>probe.event(JSON.stringify({phase,...detail}));
  let surface = "dashboard";
  const frame = async (code) => JSON.parse(await probe.evaluate(JSON.stringify({ surface,
    script: `(()=>{const q=(s)=>document.querySelector(s);return (${code});})()`,
  })));
  const mouse = (selector, host = false) => probe.pointer(JSON.stringify({ selector, host }));
  const click = async (selector) => {
    if (surface === "dashboard") {
      await until(() => frame(`!!q(${JSON.stringify(selector)})`), selector);
      await mouse(selector);
      return;
    }
    await until(() => frame(`q(${JSON.stringify(selector)})?.click() === undefined && !!q(${JSON.stringify(selector)})`), selector);
  };
  const task=async (index,number,priority)=>JSON.parse(await probe.task(JSON.stringify({index,number,priority})));
  const sharedSource=async (surface)=>JSON.parse(await probe.asset(JSON.stringify({ surface,
    html: [...document.querySelectorAll("iframe")].find((frame) => frame.title.startsWith("workLog ")
      && (frame.title === "workLog Dashboard") === (surface === "dashboard")).srcdoc,
  })));
  const showLabel=(label)=>[...document.querySelectorAll('button,[role="button"]')].find((button)=>
    [button.getAttribute("aria-label"),button.getAttribute("title"),button.textContent.trim()].includes(label)
      && button.getBoundingClientRect().width>0 && button.getBoundingClientRect().height>0);
  void (async()=>{
    await observe("setup",{version:ctx.app.version});
    await until(()=>ctx.layout.ready() && ctx.servers.get("sidecar")?.connected,"ui ready");
    for(const tab of ctx.tabs.list()) ctx.tabs.close(tab.key);
    await until(()=>ctx.router.path()==="/" && !ctx.sessions.current() && !ctx.tabs.list().length,"home route");
    const title=await until(()=>showLabel("workLog Dashboard"),"visible dashboard button");
    await observe("visible-dashboard",{tag:title.tagName,path:ctx.router.path(),tabs:ctx.tabs.list().length});
    title.click();
    await until(()=>frame(`document.documentElement.dataset.surface === "dashboard" && document.body.innerText.includes("Fixture A") && document.body.innerText.includes("Fixture B")`),"actual Dashboard data");
    await observe("dashboard-data",{asset:await sharedSource("dashboard"),visible:await frame('document.body.innerText.slice(0,300)')});
    const todoFilter = '#filters [data-status="TODO"]';
    await frame(`(() => {
      document.addEventListener("pointerdown", (event) => {
        document.body.dataset.dashboardPointer = String(event.isTrusted);
      }, { once: true });
      return true;
    })()`);
    await mouse(todoFilter);
    await until(async () => {
      const state = await frame(`({ pointer: document.body.dataset.dashboardPointer,
        selected: q(${JSON.stringify(todoFilter)})?.getAttribute("aria-pressed") })`);
      if (!state) throw new Error("Dashboard 在首次真实鼠标点击后关闭");
      return state.pointer === "true" && state.selected === "true";
    }, "Dashboard 鼠标点击筛选");
    await observe("dashboard-trusted-pointer", { filter: "TODO", stayedOpen: true });
    await click('#filters [data-status="all"]');
    const fixtures=JSON.parse(await probe.fixtures());
    const add=async (index,surface)=>{
      await click("#add-task");
      await until(()=>frame(`!!q('#task-dialog[open] #dialog-fields input')`),"new task form");
      const project=fixtures[index].project_id;
      await frame(`(()=>{const select=q('#dialog-fields select');if(select)select.value=${JSON.stringify(project)};q('#dialog-fields input').value='Added from ${surface}';q('#dialog-submit').click();return true;})()`);
      await until(()=>frame(`document.body.innerText.includes('Added from ${surface}')`),"added task loaded");
      await observe("create",{surface,task:await task(index,"T002")});
    };
    await add(0,"dashboard");
    await mouse('[aria-label="workLog Dashboard"] > button', true);
    await until(() => !document.querySelector('iframe[title="workLog Dashboard"]'), "Dashboard 鼠标关闭");
    const openSidebar=async (index)=>{
      surface = "project";
      const selected=fixtures[index];
      ctx.tabs.open({server:"sidecar",id:selected.sessionId},{focus:true});
      const session=await until(()=>ctx.sessions.current()?.id===selected.sessionId && ctx.sessions.current(),"session route");
      await observe("session",{directory:session.directory,id:session.id});
      const menu=await until(()=>ctx.layout.side.menu().find((item)=>item.title==="workLog Project"||item.title==="workLog 项目侧栏"),"workLog menu contribution");
      if (ctx.layout.state("worklog-opencode-gui:project",session)==="closed") {
        if (!ctx.layout.side.opened(session)) ctx.layout.side.toggle(session);
        const entry=await until(()=>showLabel(menu.title),"visible side entry");
        entry.click();
      }
      await until(()=>frame(`document.documentElement.dataset.surface === "project" && document.body.innerText.includes('Fixture ${index===0?'A':'B'} T001')`),"actual sidebar data");
      await observe("sidebar-data",{directory:session.directory,asset:await sharedSource("project"),text:await frame('document.body.innerText.slice(0,200)')});
    };
    await openSidebar(0);
    let index=0;
    const control=(kind)=>`.task[data-key="${fixtures[index].project_id}/${fixtures[index].task.task_id}"] .task-${kind}`;
    async function status(action,expected,dialogValues) {
      await click(control("status"));
      await click(`#status-menu [data-action="${action}"]`);
      if(dialogValues) {
        await until(()=>frame(`!!q('#task-dialog[open] #dialog-fields textarea') && !q('#dialog-submit').disabled`),`${action} dialog`);
        await frame(`(()=>{const fields=[...document.querySelectorAll('#dialog-fields textarea')];${JSON.stringify(dialogValues)}.forEach((value,index)=>fields[index].value=value);q('#dialog-submit').click();return true;})()`);
      }
      const resulting=await until(async()=>{const t=await task(index,"T001");return t.display_status===expected && t;},`saved ${action}`);
      await until(()=>frame(`!q('#refresh').disabled`),"read-back complete");
      await observe("status",{surface,action,status:resulting.display_status,revision:resulting.revision});
    }
    const priority=async (name,expected)=>{
      await click(control("priority"));
      await click(`#status-menu [data-priority="${name}"]`);
      const rv=await until(async()=>{const t=await task(index,"T001");return t.priority===expected && t;},`saved priority ${name}`);
      await until(()=>frame(`!q('#refresh').disabled`),"priority read-back");
      await observe("priority",{surface,expected,readback:rv.priority,revision:rv.revision});
    };
    async function statusTransitions() {
      await status("start","IN_PROGRESS");
      await status("pause","PAUSED");
      await status("resume","IN_PROGRESS");
      await status("setBlocker","BLOCKED",["Fixture blocker","Fixture solution"]);
      await status("resolveBlocker","IN_PROGRESS",["Fixture resolved"]);
      await status("cancel","CANCELLED",["Fixture cancellation"]);
      await status("reopen","TODO",["Fixture reopened"]);
    }
    await statusTransitions();
    await priority("p1","p1");
    await priority("unset",null);
    await task(0,"T001","p3");
    await click(control("priority"));
    await click('#status-menu [data-priority="p1"]');
    await until(()=>frame(`q('#refresh-status').textContent.includes('变化') || q('#refresh-status').textContent.includes('changed')`),"revision conflict feedback");
    await observe("conflict",{readback:(await task(0,"T001")).priority});
    await until(()=>frame(`!q('#refresh').disabled`),"conflict refresh");
    await probe.lock();
    try {
      await click(control("priority"));
      await click('#status-menu [data-priority="p2"]');
      await until(()=>frame(`q('#refresh').disabled && /保存|saving/i.test(q('#refresh-status').textContent)`),"A write submitted");
      const beforeA=await task(0,"T001");
      const beforeB=await task(1,"T001");
      if(beforeA.priority!=="p3" || beforeB.priority!==null) throw new Error("Bad in-flight fixture");
      await openSidebar(1);
      await observe("switched-in-flight",{aBefore:beforeA.priority,bBefore:beforeB.priority,bTask:beforeB.task_id});
    } finally { await probe.unlock(); }
    const afterA=await until(async()=>{const t=await task(0,"T001");return t.priority==="p2" && t;},"original project committed");
    const afterB=await task(1,"T001");
    if(afterB.priority!==null || afterB.revision!==1) throw new Error("In-flight request leaked into B");
    const bNotice=await frame(`q('#refresh-status').textContent`);
    if(/已保存|priority saved/i.test(bNotice)) throw new Error("In-flight receipt leaked into B");
    await observe("inflight-readback",{aPriority:afterA.priority,aTask:afterA.task_id,bPriority:afterB.priority,bRevision:afterB.revision,bNotice});
    await openSidebar(0);
    await status("complete","DONE",["Fixture completed"]);
    await openSidebar(1);
    await add(1,"project");
    surface = "dashboard";
    (await until(() => showLabel("workLog Dashboard"), "Dashboard button with a chat")).click();
    await until(()=>frame(`document.documentElement.dataset.surface === "dashboard" && document.body.innerText.includes("Fixture B T001")`),"Dashboard re-opened");
    await click('#filters [data-status="all"]');
    index=1;
    await statusTransitions();
    await priority("p1","p1");
    await priority("unset",null);
    await status("complete","DONE",["Fixture completed from Dashboard"]);
    await observe("done",{a:await task(0,"T001"),b:await task(1,"T001")});
  })().catch(async (error)=>observe("error",{message:String(error), frames:[...document.querySelectorAll("iframe")].map(f=>f.title), dialog:document.querySelector('[aria-label="workLog Dashboard"]')?.innerText, panel:await frame("document.body.innerText.slice(0,350)").catch(()=>null)}));
}
module.exports={default:Plugin.define({main,setup})};
