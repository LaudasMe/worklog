# 安装和使用 workLog Skill

workLog Skill 安装包包含 Codex 与 OpenCode 共用的 `SKILL.md`、Python CLI 和命令参考。使用 Python 3.9 或以上和 `unzip`。随包 CLI 支持任务状态、优先级、项目登记及公开只读协议 2.0。公开读取要求 schema 4。用 `context` 和 `summaries` 可以先核对旧库，再按需要备份和升级。命令见包内 `references/commands.md`。

## 取得安装包

从 workLog 仓库根目录构建到单独的目录。脚本输出安装包的绝对路径。已有基于当前源码构建的安装包时可以直接解压。

```sh
PACKAGE_DIR="$(mktemp -d)"
python3 -B scripts/package_skill.py --output-dir "$PACKAGE_DIR"
```

## 项目级安装

首次安装请选一个独立的 Git 项目根。把以下路径替换为实际绝对路径。两端从这个根目录或子目录启动新会话。ZIP 自带 `worklog/` 目录。

```sh
SKILL_ZIP="/absolute/path/to/worklog-skill-v0.4.0.zip"
PROJECT_ROOT="/absolute/path/to/project-root"
SKILLS_DIR="$PROJECT_ROOT/.agents/skills"
mkdir -p "$SKILLS_DIR"
test ! -e "$SKILLS_DIR/worklog" &&
  unzip -n "$SKILL_ZIP" -d "$SKILLS_DIR" &&
  python3 -B "$SKILLS_DIR/worklog/scripts/worklog.py" release-version
```

命令会先检查是否已有 workLog 安装。当前发布版本为 `v0.4.0`，CLI 的 `--version` 输出为 `workLog CLI protocol 2.0`。升级时先把新版本解压到临时目录，用解压后的 CLI 执行 `release-version` 核对发布版本和协议，再整体替换 `.agents/skills/worklog/` 并开启新会话。每个工作项目的 SQLite 数据位于自己的 `.worklog/`。两端的登记共用 `~/.local/share/worklog/projects.json`，两者均独立于 Skill 安装目录。移除 Skill 时只移除安装的 `.agents/skills/worklog/` 目录。

如要多项目共用，可用相同方法解压到 `~/.agents/skills/` 并开启新会话。项目和用户级目录只保留一个用于当前验收的 `worklog` 定义，便于核对实际加载版本。两个客户端仍通过你确认的项目路径定位数据。

## 使用

在 Codex 中通过 Skill 选择器选择 `worklog`。在 OpenCode 中请 Agent 通过内置 `skill` 工具加载 `worklog`。从项目根发起请求时可直接说「当前项目」。跨目录或有多个可能项目时给出项目绝对路径。

- 「查询当前用的是哪个 workLog 版本。」
- 「检查 workLog 是否有更新。」
- 「查看这个项目的 T001 进展和最近交接。」
- 「用 workLog 摘要发现当前项目中的任务，读取项目和任务上下文。继续其中待做的验证，并把确认的结果写回。」
- 「开始 T001。」
- 「把 T001 的优先级设为 p1，再看看当前待推进的任务。」
- 「暂停 T001，保留接手；把进展交给另一个会话。」
- 「接手 T001，先告诉我上次交接的下一步。」
- 「将这些检查结果记到 T001，下一步是核对零金额。」
- 「引用刚才保存的验证记录，把 T001 标记为完成。」
- 「把 T001 交接出去，说明下一位应该核对什么。」

空项目可明确请 Skill 「初始化 workLog，项目名称为北辰样例」并「新建一个任务，标题为核对金额」。数据已经初始化时，查看请求可直接执行。开始任务无需接手。标记完成时引用已有日志。需要交接时，当前会话须持有对应任务的接手。查询已完成任务使用 `context --task T001`。

版本查询和更新检查可在任意目录执行。用实际加载的 Skill 目录定位下列脚本。更新检查会访问 GitHub Releases，返回对应正式版及链接，由用户手动下载和安装。

```sh
python3 -B "/absolute/path/to/worklog/scripts/worklog.py" release-version
python3 -B "/absolute/path/to/worklog/scripts/worklog.py" check-update
```

发布版本缺失时会显示「无法识别」。本机版本未知或远程查询失败时，更新状态显示「无法判断」。再次执行 `check-update` 可重试。

Codex 聊天第一次使用 workLog 或查询当前版本时，Skill 会在单独的命令调用中为 `release-version` 加上 `env WORKLOG_CODEX_SKILL=1`。同一聊天的 workLog 侧栏可据此识别 Skill 使用的版本。OpenCode 中请核对 `skill` 工具返回的安装目录和 CLI 实际命令路径。

## 从新会话复跑验收

1. 创建两个合成 Git 项目并安装同一个 ZIP。用仓库的 `examples/context_seed.py` 准备第一个项目，传入 `--alternate` 准备第二个。记录各自的项目 ID、任务 ID、数据库 SHA-256 和 2.0 快照。
2. 在 Codex 和 OpenCode 分别开启新会话，通过各自原生的 Skill 入口加载安装包并请求只读查询。核对安装位置、任务 ID、状态和摘要，比较数据库 SHA-256 与快照。
3. 在北辰项目根开启一个新会话。只告诉它使用 Skill，先通过摘要找到当前任务，再读项目和任务上下文，根据待办执行实际工作并写回。另开新会话，只读复查摘要、当前上下文、已关闭事项、尚未关闭的问题和被替换决定的原文。
4. 检查北辰的新进展与实际文件，比较南星数据库的前后哈希和项目数据。跨客户端接棒可单独用 `claim`、`checkpoint`、`handoff` 复跑。
5. 在单独的合成项目里新建任务，请 Skill 显式开始、保存一条带任务关联的验证日志，再引用该日志标记完成。另开只读会话，通过原生入口加载同一份 Skill，筛选 `DONE` 后用 `context --task T001` 获取完成依据，按其命令和 ID 读回原文。核对新会话读取前后的数据库哈希。

替换一端安装包后，从两端用 `projects list` 核对原登记和数据库身份，再替换另一端并重复。合成测试保留客户端的 Skill 加载和调用回执。只读查询与显式写入分别验收。客户端步骤可使用上述自然语言，命令字段见安装目录下的 `references/commands.md`。
