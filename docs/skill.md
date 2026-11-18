# 安装和使用 workLog Skill v0.1

这个本机安装包包含一份 Codex 与 OpenCode 共用的 `SKILL.md`、现有 Python CLI 和命令参考。使用 Python 3.9 或以上和 `unzip`，两个客户端各自用本机 Python 调用同一份工具。Skill 版本为 0.1.0，随包 CLI 使用 workLog 公开协议 1.0。

## 取得安装包

从 workLog 仓库根目录构建。输出为 `dist/worklog-skill-v0.1.0.zip`。已取得安装包时可以直接解压。

```sh
python3 -B scripts/package_skill.py
```

## 项目级安装

首次安装请选一个独立的 Git 项目根。把以下路径替换为实际绝对路径。两端从这个根目录或子目录启动新会话。ZIP 自带 `worklog/` 目录。

```sh
SKILL_ZIP="/absolute/path/to/worklog-skill-v0.1.0.zip"
PROJECT_ROOT="/absolute/path/to/project-root"
SKILLS_DIR="$PROJECT_ROOT/.agents/skills"
mkdir -p "$SKILLS_DIR"
test ! -e "$SKILLS_DIR/worklog" &&
  unzip -n "$SKILL_ZIP" -d "$SKILLS_DIR" &&
  python3 -B "$SKILLS_DIR/worklog/scripts/worklog.py" --version
```

命令会先检查是否已有 worklog 安装。升级时把新版本先解压到临时目录，整体替换 `.agents/skills/worklog/`，再开启新会话。每个工作项目的 SQLite 数据位于自己的 `.worklog/`，与安装目录独立。移除 Skill 时只移除安装的 `.agents/skills/worklog/` 目录。

如要多项目共用，可用相同方法解压到 `~/.agents/skills/` 并开启新会话。项目和用户级目录只保留一个用于当前验收的 `worklog` 定义，便于核对实际加载版本。两个客户端仍通过你确认的项目路径定位数据。

## 使用

在 Codex 里点名 `$worklog`，在 OpenCode 里点名 `@worklog`，再写自然语言请求。跨目录或有多个项目时给出要操作的项目绝对路径。对于「这个项目」，确认是正确的根目录。

- 「查看这个项目的 T001 进展和最近交接。」
- 「接手 T001，先告诉我上次交接的下一步。」
- 「将这些检查结果记到 T001，下一步是核对零金额。」
- 「把 T001 交接出去，说明下一位应该核对什么。」

空项目可明确请 Skill 「初始化 workLog，项目名称为北辰样例」并「新建一个任务，标题为核对金额」。数据已经初始化时，查看请求可直接执行。单独要求记录或交接时，当前会话须持有对应任务的接手。

## 从新会话复跑验收

1. 在一个合成 Git 工作区安装一次。建两个现有目录 A 和 B，分别显式初始化后创建各自的 T001。标题设为不同值。用安装目录的 CLI 检查两边的初始快照，记录项目 ID、任务 UUID 和水位。
2. 在 Codex 和 OpenCode 分别开启新会话，点名 Skill 并请求查看两个目录中的 T001。校对各自的任务标题和项目身份、读取回执和前后水位。
3. 再开 Codex 新会话，指明 A 的绝对路径，让它接手 T001，保存一段含中文和代码块的进展并交接，写清下一步。另开 OpenCode 新会话读取 A 的交接和原文，接手同一任务并记下新的进展。
4. 比较最终两份快照。A 的稳定任务 ID 与初始值相同，其进展和接手来源正确。B 的快照与初始值相同。工作目录可以和 A、B 都不同，项目以请求中的显式路径确定。

合成测试保留客户端的 Skill 加载和调用回执。只读查询与显式写入分别验收。客户端步骤可使用上述自然语言，命令字段见安装目录下的 `references/commands.md`。
