# workLog

workLog 帮你在 Codex、OpenCode 或同一个客户端的新会话里接着做一个项目。会话可以从项目目录找到任务，读回目标、决定、进展和待办，完成工作后把结果记下来。

你可以按状态和优先级查任务，记录 Markdown 原文和完成依据，也可以暂停、取消、归档、重开或把任务交给下一个会话。每个项目各有一个数据库，放在自己的 `.worklog/` 目录里。

## 第一次使用

1. 准备 Python 3.9 或以上。从 [v0.3.0 发布页](https://github.com/LaudasMe/worklog/releases/tag/v0.3.0) 下载 Skill ZIP，也可以在 v0.3.0 源码目录运行 `python3 -B scripts/package_skill.py` 构建。
2. 按[安装说明](docs/skill.md)把 ZIP 解压到客户端能找到的位置。已有 schema 1、2、3 的项目可先用本地上下文核对，再按[备份与升级步骤](docs/context-cli.md#备份和升级)升到 schema 4。
3. 从项目根目录开一个新会话，加载 `worklog` Skill 后说「找出当前项目还在处理的任务，读一下上下文，告诉我下一步」。

[用户指南](docs/user-guide.md)包含新会话读取、写回和复查，以及从创建 T001 到跨客户端接手的完整示例。上一版仍可从 [v0.2.0 标签](https://github.com/LaudasMe/worklog/tree/v0.2.0)获取。

## 从命令行使用

[上下文 CLI](docs/context-cli.md)介绍任务摘要、项目与任务上下文、批量写回和旧库升级。[CLI 说明](docs/cli.md)介绍任务和交接流程。公开[只读接口](docs/read-protocol.md)仅支持协议 2.0，可读取快照、增量和历史原文。

## 运行测试

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

请在保留 `v0.1.0`、`v0.2.0` 标签及提交历史的完整 Git 克隆中运行测试。测试用历史源码生成临时数据库，检查备份、升级、回滚和恢复，以及任务生命周期、优先级、项目登记、公开只读协议、跨目录接力和 ZIP 安装。

许可证见 [LICENSE](LICENSE)。
