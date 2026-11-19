# 安装 workLog Skill

安装包内含 Codex 和 OpenCode 共用的 Skill、Python 命令行工具和命令参考。需要 Python 3.9 或以上，以及 `unzip`。

## 准备 ZIP

有安装包时可以直接使用。从 workLog 源码目录运行下面的命令可生成安装包。

```sh
python3 -B scripts/package_skill.py
```

当前源码生成 `dist/worklog-skill-context-candidate.zip`。用 v0.1.0 标签构建时，文件名为 `worklog-skill-v0.1.0.zip`。

## 选择安装位置

| 你想在哪儿使用 | 安装位置 |
| --- | --- |
| 只在一个 Git 仓库使用，Codex 和 OpenCode 共用 | 该 Git 仓库根目录的 `.agents/skills/` |
| 在本机 Codex 的多个项目里使用 | `~/.agents/skills/` |
| 在本机 OpenCode 的多个项目里使用 | `~/.config/opencode/skills/` |

下方用 Codex 的本机目录举例。把 `SKILL_ZIP` 换成 ZIP 的实际路径。给 OpenCode 安装时，把 `SKILLS_DIR` 换成 `"$HOME/.config/opencode/skills"`。

```sh
SKILL_ZIP="/path/to/worklog-skill-context-candidate.zip"
SKILLS_DIR="$HOME/.agents/skills"
mkdir -p "$SKILLS_DIR"
test ! -e "$SKILLS_DIR/worklog" &&
  unzip -n "$SKILL_ZIP" -d "$SKILLS_DIR" &&
  python3 -B "$SKILLS_DIR/worklog/scripts/worklog.py" --version
```

解压后，`worklog/` 目录里应能看到 `SKILL.md`。最后一条命令会显示 CLI 的协议版本。项目里已有 workLog 时，可以直接查询。新项目要明确要求初始化，数据库会建在那个项目的 `.worklog/` 下。

## 试一下

开一个客户端新会话。Codex 用 Skill 选择器选择 `worklog`，OpenCode 可直接请它用原生 `skill` 工具加载 `worklog`。如果同一台机器有多个安装位置，先核对客户端实际加载的 Skill 路径。

> `$worklog` 初始化 `<项目根目录>` 的 workLog，项目名称为「示例项目」。建一个任务「核对订单合计」。

> `@worklog` 查看 `<项目根目录>` 的 T001。告诉我当前阶段和下一步。

从项目根目录开的新会话可以直接说「用 workLog 列出当前项目的任务，读取项目和任务上下文」。从其他目录启动时，补充项目绝对路径。

把 `<项目根目录>` 换成已有项目文件夹的完整路径，把「示例项目」改成你的项目名。想验证交接，可以在 Codex 接手、记一条进展并交接，然后在 OpenCode 新会话查看原文并接手。要验证项目之间的数据隔离，可以用两个有不同标题的 T001，按路径分别查询。

## 更新或移除

更新时可以先把新版本解压到临时目录，再用其中的 `worklog/` 替换已有安装目录，随后开一个新会话。移除时删除安装的 `worklog/` 目录即可。任务和进展保存在各项目的 `.worklog/` 里。

如果项目库来自 v0.1.0，可以先用候选版查询。首次写入上下文前，用候选版 CLI 运行 `backup --output <备份文件>`，再运行 `upgrade --backup <备份文件>`。数据库备份和升级按项目执行。

安装目录中的 `references/commands.md` 有命令参数和 JSON 示例。
