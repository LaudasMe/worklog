# workLog 用户指南

workLog 在项目里保存任务、待办、进展和交接记录。你可以在 Codex、DeepSeek Harness 或 OpenCode 中保存进展，再从另一个客户端打开同一个项目，读回原文并继续工作。

本指南适用于 **CLI／Skill v0.6.0**、**Codex GUI 0.3.0**、**DSH 插件 0.1.0** 和 **OpenCode GUI 0.1.0**。

## 1. 安装

| 使用方式 | 安装入口 |
| --- | --- |
| 在聊天中使用 workLog | [安装通用 CLI／Skill](#安装通用-skill) |
| 在 macOS 的 Codex 桌面使用面板 | [安装 Codex GUI](#在-macos-上安装-codex-gui)，完整包随附 CLI／Skill |
| 在 DeepSeek Harness 桌面使用面板 | [安装 DSH 插件](#在-deepseek-harness-桌面安装插件)，插件随附 CLI／Skill |
| 在 OpenCode 桌面使用面板 | 先[安装通用 CLI／Skill](#安装通用-skill)，再[安装 OpenCode GUI](#在-opencode-桌面安装-gui) |

### 安装通用 Skill

在 macOS 或 Linux 上准备 Python 3.9 或更新版本和 `curl`。在终端运行以下命令，安装到 `~/.agents/skills/worklog/`。

```sh
(worklog_installer="$(curl -fsSL https://raw.githubusercontent.com/floohubAI/worklog/main/scripts/install-cli.sh)" && /bin/bash -c "$worklog_installer")
```

脚本校验 v0.6.0 发布 ZIP。发现用户级安装或 Codex 旧路径的安装时，脚本显示实际路径并保留原版本。装好后开启新聊天。在 Codex 中用 Skill 选择器选中 `worklog`，在 OpenCode 中请 Agent 用内置的 `skill` 工具加载。 [发布页](https://github.com/floohubAI/worklog/releases/tag/v0.6.0) · [手动安装与升级](skill.md)

### 在 macOS 上安装 Codex GUI

准备支持本地插件的 Codex 桌面端和 Python 3.9 或更新版本。本次验证使用 Codex Desktop `26.1120.50957` 和随附 App Server `0.171.0-alpha.13.2`。在交互式终端执行下面的命令，安装 Dashboard、项目侧栏及随包的 CLI 和 Skill。

```sh
(worklog_installer="$(curl -fsSL https://github.com/floohubAI/worklog/releases/download/gui-codex-v0.3.0/install-codex.sh)" && /bin/bash -c "$worklog_installer")
```

脚本会下载并校验完整安装包。已有 Skill 需要替换或迁移时，安装器会在终端提示确认，并在迁移时备份原安装。装好后重启 Codex。项目级 Skill 由各项目单独维护，可以从新聊天里查询实际使用的版本。 [Codex GUI 0.3.0 发布页](https://github.com/floohubAI/worklog/releases/tag/gui-codex-v0.3.0) · [迁移与恢复说明](../gui/codex/INSTALL.md)

也可下载[完整安装包](https://github.com/floohubAI/worklog/releases/download/gui-codex-v0.3.0/worklog-codex-gui-v0.3.0-full.zip)和 [SHA-256 校验文件](https://github.com/floohubAI/worklog/releases/download/gui-codex-v0.3.0/SHA256SUMS)，在下载目录运行 `shasum -a 256 -c SHA256SUMS --ignore-missing`。确认完整 ZIP 的校验结果为 `OK`，解压后在包目录运行 `python3 -B install.py`。需要指定 Codex CLI 时，可追加 `--codex /absolute/path/to/codex`。

### 手动安装 Skill

从发布页把 `worklog-skill-v0.6.0.zip` 和 `SHA256SUMS` 下载到同一目录。准备 Python 3.9 或更新版本和 `unzip`，将下面两处路径换成实际绝对路径。 [CLI／Skill 发布页](https://github.com/floohubAI/worklog/releases/tag/v0.6.0)

```sh
PROJECT_ROOT="/absolute/path/to/your-project"
DOWNLOAD_DIR="/absolute/path/to/downloads"
SKILLS_DIR="$PROJECT_ROOT/.agents/skills"

(cd "$DOWNLOAD_DIR" && shasum -a 256 -c SHA256SUMS) &&
  mkdir -p "$SKILLS_DIR" &&
  test ! -e "$SKILLS_DIR/worklog" &&
  unzip -n "$DOWNLOAD_DIR/worklog-skill-v0.6.0.zip" -d "$SKILLS_DIR" &&
  python3 -B "$SKILLS_DIR/worklog/scripts/worklog.py" release-version
```

手动安装用户级 Skill 时，将 `SKILLS_DIR` 改为 `"$HOME/.agents/skills"`。OpenCode GUI 依赖这个位置。若通用安装脚本只发现 Codex 的旧路径，可在这里手动安装新 Skill。目标目录已有 `worklog` 时，按[检查更新与升级](#6-检查更新与升级)处理。 [Skill 安装说明](skill.md)

### 在 DeepSeek Harness 桌面安装插件

已验证 DeepSeek Harness Desktop **0.2.2-rc.5**。准备 Python 3.9 或更新版本，并通过应用的「管理 dsh 命令」启用 `dsh`。从同一发布页下载 `worklog-dsh-0.1.0.tgz` 和 `SHA256SUMS`，在下载目录运行校验。 [DSH 插件发布页](https://github.com/floohubAI/worklog/releases/tag/dsh-v0.1.0)

```sh
shasum -a 256 -c SHA256SUMS
```

校验通过后，退出 DeepSeek Harness。在同一个下载目录运行安装命令。

```sh
dsh plugin --profile desktop add "$PWD/worklog-dsh-0.1.0.tgz" --offline --ignore-scripts
```

重启应用。从左侧打开 **workLog Dashboard**。在本地会话中，从右侧栏的添加面板入口打开 **workLog 项目侧栏**。插件已随附 CLI／Skill v0.6.0。若项目有同名 Skill，DSH 会优先加载项目自己的 Skill。 [DSH 安装说明](../plugins/dsh/README.md)

### 在 OpenCode 桌面安装 GUI

已验证 macOS arm64 的 OpenCode Desktop **2.4.2**。先将 CLI／Skill v0.6.0 安装到 `~/.agents/skills/worklog/`。如果通用脚本只识别到 Codex 旧路径，按手动安装步骤写入用户级目录。下载 `worklog-opencode-gui-0.1.0.tgz` 和同页 `SHA256SUMS`，在下载目录运行 `shasum -a 256 -c SHA256SUMS`。 [手动安装 Skill](#手动安装-skill) · [OpenCode GUI 发布页](https://github.com/floohubAI/worklog/releases/tag/gui-opencode-v0.1.0)

将 TGZ 解包到固定目录，保留包内的 `package/`。在用户级的 `opencode.json` 或 `opencode.jsonc` 的 `plugins` 数组里登记解包后 `package/` 的绝对路径，并保留现有配置。默认配置目录为 `~/.config/opencode/`。重启后，可在没有聊天时从主页标题栏打开 **workLog Dashboard**，也可在本地聊天中从右侧栏「＋」打开 **workLog 项目侧栏**。 [OpenCode 安装与排查](../gui/opencode/README.md)

## 2. 从创建任务到记录进展

从要管理的项目根目录开启聊天并加载 `worklog`。项目已经启用 workLog 时，可以直接查看任务。项目管理目录和代码仓分开时，请明确给出存放 workLog 数据的项目目录。下面用 `T001` 举例，请以客户端返回的编号为准。

1. **首次启用并创建任务。**「为当前项目初始化 workLog，项目名用『示例项目』。创建任务『核对订单合计』，优先级设为 p1。」
2. **开始任务。**「开始 T001。」任务进入进行中，可以直接保存进展。需要在多个聊天之间接力时，可以显式接手。
3. **保存已确认的进展。**「给 T001 记一条进展。已核对两笔订单，金额 2 和 3，合计 5。下一步加入一笔零金额订单，再算一次。」如果要保存验证输出、Markdown 或代码块，可以把原文一起提供。
4. **从新聊天继续。**「用 workLog 列出当前项目还没结束的任务，查看 T001 的进展、待办、阻塞和下一步。只查询。」你也可以单独让它读回某条进展或交接的原文。

在有多个项目的聊天中，给出项目根目录的绝对路径和任务编号。每个项目独立编号，因此两个项目都可以有 `T001`。

### 在 Codex、DeepSeek Harness 和 OpenCode 之间交接

1. 在 Codex 中请 workLog「接手 T001，先读最近进展」。如果还有其他聊天正在跟进，请先确认是否接替。
2. 在同一个聊天中说「交接 T001。已确认两笔普通订单合计 5，请下一位加入一笔零金额订单，再核对合计」。交接会保存原文和下一步，并结束本次接手。
3. 在 DeepSeek Harness 从同一项目根目录开启本地会话并加载插件随附的 Skill。先说「查看 T001 最近一次交接的原文和下一步」，再说「接手 T001」。验证后请 workLog 保存结果。
4. 在 OpenCode 从同一项目根目录开启新会话并加载 Skill，查询 T001，读回 DSH 保存的结果。下一位需要继续写入时，由 DSH 先交接，或由你明确确认接替当前接手。

进行中的任务交接后仍为进行中。暂停的任务在交接和接手后仍为暂停，可以显式恢复后继续推进。

## 3. 在 Codex 侧栏查看任务

1. 从包含 workLog 数据的项目根目录或其子目录开启本地 Codex 聊天。如果还是草稿，先发一条消息建立聊天。
2. 在聊天右侧选择「＋ → 更多工具 → 插件和 MCP → workLog 项目侧栏」。如果你曾安装早期验证插件，它的入口叫「workLog 任务列表」。
3. 在任务列表中按状态筛选，直接看「当前进展」和「下一步」。点任务标题，展开「上次交接」「待办」和「全部历史」。
4. 点「查看交接原文」可以核对交接当时留下的内容和下一步。「全部历史」按时间倒序展示进展、交接、决策和状态变更，可展开单条原文。「待办」包含未完成事项和可展开的已处理事项。

侧栏支持 schema 4 的项目数据库，可添加任务、切换状态和确认完成。「全部」显示当前项目所有未归档任务。用聊天里的 Skill 保存进展后，点侧栏的「刷新」查看新结果。

从 Codex 主侧栏应用列表打开 **workLog Dashboard**，可查看已登记项目的任务。两个面板都可组合筛选任务状态和优先级。Dashboard 还可筛选项目。Dashboard 每页 20 条，项目侧栏每页 12 条。用列表底部的箭头翻页，切换筛选会回到第一页。 [Codex GUI 安装](#在-macos-上安装-codex-gui)

两个入口的「继续任务」都会在项目管理目录打开可编辑的聊天草稿。发送预填草稿即授权该聊天跟进对应任务。整项任务完成仍需单独确认。

点击任务编号旁的优先级图标可选 `P0` 至 `P3` 或清空。短横线表示未设置。顶部的状态和优先级各选一项，列表显示同时满足两个条件的任务。

## 4. 管理任务状态、优先级和完成依据

| 状态 | 含义 |
| --- | --- |
| 待开始 `TODO` | 任务已创建，可以显式开始。接手待开始的任务时也会将它开始。 |
| 进行中 `IN_PROGRESS` | 任务已开始，可能有聊天正在跟进，也可能暂时无人接手。 |
| 受阻 `BLOCKED` | 待开始或进行中的任务存在仍有效的任务级阻塞。解除后会恢复显示原有状态。 |
| 已暂停 `PAUSED` | 暂停会保留当前接手。任务同时存在阻塞时仍显示为已暂停。显式恢复后，按阻塞是否解除显示为受阻或进行中。 |
| 已完成 `DONE` | 已保存完成范围及可查回的依据。已有接手会同时结束。 |
| 已取消 `CANCELLED` | 已记录取消原因。之前的任务历史仍可查阅。 |

你可以请 workLog 设置 `p0` 至 `p3` 的优先级、清除优先级或按优先级筛选任务。已完成和已取消的任务可以归档，也可以移出归档。重开时请写明原因和依据，任务会回到待开始并移出归档。如果要恢复历史待办或阻塞，可以从 workLog 返回的可恢复事项中选出需要带回的条目。

### 标记完成

先保存一条与任务相关的有效进展，或确认已有可引用的进展或交接记录。标记完成时说明实际完成的范围，引用至少一条已保存的依据。任务还有有效阻塞时，先处理阻塞并写回解决结果。仍开放的任务待办要逐条说明如何处置，要更新待办状态时请明确要求保存处理结果。

> 引用刚才保存的零金额验证记录，将 T001 标记为完成。完成范围包括普通订单和零金额订单的合计验证。先检查有没有尚未处理的阻塞或待办。

完成后，可以请 workLog「查看 T001 的完成时间、完成范围和依据，打开引用的验证记录原文」。 [完成规则与 CLI 示例](context-cli.md#开始和完成任务)

## 5. 在终端查询项目

把 `CLI` 换成实际安装的 `scripts/worklog.py` 的绝对路径。使用源码时，可以指向仓库根目录的 `worklog.py`。CLI 默认按当前目录找数据库。从子目录或其他目录调用时，把 `--project` 放在子命令前。

```sh
CLI="/absolute/path/to/worklog/scripts/worklog.py"
PROJECT_ROOT="/absolute/path/to/your-project"

python3 -B "$CLI" --project "$PROJECT_ROOT" context
python3 -B "$CLI" --project "$PROJECT_ROOT" summaries --type task --view pending
python3 -B "$CLI" --project "$PROJECT_ROOT" context --task T001
python3 -B "$CLI" --project "$PROJECT_ROOT" summaries --type task --status DONE
python3 -B "$CLI" --project "$PROJECT_ROOT" summaries --type task --view archived
python3 -B "$CLI" projects list
```

从任务上下文获取记录 ID 后，可以用 `record-get <记录 ID>` 查回上下文记录原文，用 `entry-get <条目 ID>` 查回接力进展或交接原文。终端写入命令通过标准输入接收 JSON。其他程序可通过公开只读协议 2.0 读取项目数据。 [CLI 命令和示例](cli.md) · [上下文命令](context-cli.md) · [只读接口说明](read-protocol.md)

### 数据与项目路径

- 项目数据库位于项目根目录下的 `.worklog/worklog.sqlite3`。Codex、DSH 和 OpenCode 在本机使用同一个项目路径时可以接续工作。使用 Git 管理项目时，可以把 `.worklog/` 加入该项目的 `.gitignore`。
- 同一系统用户的客户端使用本机的项目登记文件 `~/.local/share/worklog/projects.json`。新项目初始化时自动登记。项目更换位置后，可用 `projects list` 核对路径和身份，再更新登记。只移除登记条目时，仍可用实际路径访问数据库。 [注册、移动和移除登记](context-cli.md#从项目登记选择升级)

## 6. 检查更新与升级

**在 Codex 侧栏中**，点击「检查更新」。结果分别显示「Codex GUI」和当前聊天使用的「CLI／Skill」的版本和更新状态。如果出现「当前使用版本无法确认」，在同一个聊天里先请 workLog「查看当前使用的 workLog 版本」，然后再点一次「检查更新」。

**在聊天中**，可以直接问「当前用的是哪个 workLog 版本」或「检查 workLog 是否有更新」。**在终端中**，使用上一节设置的 `CLI`，下面两条命令可在任意目录运行。

```sh
python3 -B "$CLI" release-version
python3 -B "$CLI" check-update
```

`release-version` 返回安装包的发布版本，例如 `v0.6.0`。`--version` 返回 CLI 协议版本，例如 `2.0`。检查到新版本后，打开对应组件的 Release 链接手动安装。单独升级 Skill 时，先把新 ZIP 解压到临时目录，运行其中的 `scripts/worklog.py release-version` 核对版本，再替换实际使用的整套 Skill 目录并开启新聊天。 [Skill 安装与更新说明](skill.md)

- **Codex GUI** 下载新版本的 `install-codex.sh`，在交互式终端运行并按提示更新。 [Codex GUI 安装](#在-macos-上安装-codex-gui)
- **DSH 插件** 退出 DeepSeek Harness，按目标版本的发布说明更新 Desktop profile 中的插件。随包 CLI 和 Skill 一起更新。 [DSH 安装](#在-deepseek-harness-桌面安装插件)
- **OpenCode GUI** 解包新版本到固定目录，在 `plugins` 中将原有 workLog 条目改为新 `package/` 的绝对路径后重启。用户级 CLI／Skill 按上面的独立升级步骤更新。 [OpenCode GUI 安装](#在-opencode-桌面安装-gui)

### 升级已有的项目数据库

如果你从较早版本升级，先用 `context` 查看 `result.schema_version`。已经是 schema 4 的项目可以直接继续使用。schema 1 至 3 仍可以用本地 `context` 和 `summaries` 查询。要在侧栏查看旧项目，或使用要求 schema 4 的命令，需要先给该项目做备份，再显式升级到 schema 4。

以下示例使用上一节设置的 `CLI` 和 `PROJECT_ROOT`。将 `PROJECT_ID` 换成该项目的 `project_id`，将 `BACKUP_PATH` 换成已存在目录下一个尚未创建的绝对文件路径。

```sh
PROJECT_ID="<project_id>"
BACKUP_PATH="/absolute/path/to/backups/project-before-upgrade.sqlite3"

python3 -B "$CLI" --project "$PROJECT_ROOT" backup --output "$BACKUP_PATH" --project-id "$PROJECT_ID" &&
  python3 -B "$CLI" --project "$PROJECT_ROOT" upgrade --backup "$BACKUP_PATH" --project-id "$PROJECT_ID"
```

升级会保留任务、历史、项目身份和原有接手。如果备份和升级之间数据库有新写入，请换一个路径重新备份，再用新备份升级。升级后用 `context` 确认 schema 版本，核对已有任务和交接。 [详细备份、升级与恢复验证](context-cli.md#备份和升级)

## 7. 遇到提示时怎么处理

| 提示 | 处理方法 |
| --- | --- |
| 项目尚未初始化（`not_initialized`） | 先核对聊天目录或 `--project` 路径。确实是首次使用时，再请 workLog 初始化。 |
| 找不到任务（`task_not_found`） | 核对项目和编号。需要找归档任务时，请查看归档列表。 |
| 侧栏没有显示项目 | 确认聊天已建立，打开它的目录与项目数据位置一致，再重试读取。旧库可先用本地 CLI 查询。 |
| 其他聊天正在跟进（`claim_conflict`） | 先查看最新进展，等原聊天交接，或明确确认接替。 |
| 接手已结束或数据已更新（`stale_run`、`revision_conflict`） | 重新读取任务和最近记录，再决定是否继续或重新接手。 |
| 无法完成任务（`invalid_evidence`、`task_blocked`、`todo_disposition_required`） | 核对已保存的依据、仍生效的阻塞和开放待办，处理后重新提交。 |
| 数据库忙（`database_busy`）或保存后未收到回执 | 用相同命令、相同内容和同一个操作 ID 重试，已保存的请求会返回原回执。 |
| 更新状态「无法判断」 | 确认聊天实际加载的版本，检查是否可以访问 GitHub Releases，再重试更新检查。 |

## 8. 用现成的示例项目练习

如果你有 workLog 源码，可以从源码仓库根目录运行下面的命令。它会创建并登记一个临时示例项目，里面已有 T001、待办、阻塞和可复算的样例数据。

```sh
DEMO_DIR="$(mktemp -d)"
git -C "$DEMO_DIR" init &&
  python3 -B examples/context_seed.py "$DEMO_DIR" &&
  printf '%s\n' "$DEMO_DIR"
```

从输出的目录开启新聊天并加载 `worklog`。先让它查询 T001 的待办和阻塞，再让它按待办复测零金额。复算结果应为 `2 + 3 + 0 = 5`。确认通过后，请它在一次写回中保存验证结果、完成对应待办并解除阻塞。另开聊天可以复查已处理事项、仍待决定的问题和原始记录。
