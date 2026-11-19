# workLog

workLog 帮你在 Codex、OpenCode 或同一个客户端的新会话里接着做一个项目。会话可以从项目目录找到任务，读回目标、决定、进展和待办，完成工作后把结果记下来。

你可以新建任务、查看进展、接手、记录 Markdown 原文，也可以把任务交给下一个会话。每个项目各有一个数据库，放在自己的 `.worklog/` 目录里。

## 第一次使用

1. 准备 Python 3.9 或以上。从 [v0.2.0 发布页](https://github.com/LaudasMe/worklog/releases/tag/v0.2.0) 下载 Skill ZIP，也可以在 v0.2.0 源码目录运行 `python3 -B scripts/package_skill.py` 构建。
2. 按[安装说明](docs/skill.md)把 ZIP 解压到客户端能找到的位置。已有 v0.1.0 项目可先查询，在首次写入上下文前按[备份与升级步骤](docs/context-cli.md#备份和升级)升级。
3. 从项目根目录开一个新会话，加载 `worklog` Skill 后说「找出当前项目还在处理的任务，读一下上下文，告诉我下一步」。

[用户指南](docs/user-guide.md)包含新会话读取、写回和复查，以及从创建 T001 到跨客户端接手的完整示例。上一版仍可从 [v0.1.0 标签](https://github.com/LaudasMe/worklog/tree/v0.1.0)获取。

## 从命令行使用

[上下文 CLI](docs/context-cli.md)介绍任务摘要、项目与任务上下文、批量写回和旧库升级。[CLI 说明](docs/cli.md)介绍原有任务和交接流程。[只读接口 1.0](docs/read-protocol.md)继续提供快照、增量和历史原文查询。

## 运行测试

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

请在保留 `v0.1.0` 标签的 Git 克隆中运行测试。测试用旧版源码生成临时数据库，检查升级与回滚、上下文和批量写回、公开只读协议、跨目录接力、项目隔离和 ZIP 构建。这些上下文功能也已在实际 Codex 和 OpenCode 新会话中验证 Skill 加载和只读查询，并在两个独立 Codex 会话中验证写回与复查。

许可证见 [LICENSE](LICENSE)。
