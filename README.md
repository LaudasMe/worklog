# workLog

workLog 是 Codex 和 OpenCode 共用的本机 Skill。它为项目记录任务、进展和交接，也提供可独立调用的 Python CLI。两个客户端可以从不同目录操作同一个项目，继续处理同一条任务。

- **查看**项目任务、最新进展和交接原文。
- **接手、记录、交接**工作，保存 Markdown 正文和明确的下一步。
- **读取结构化数据**，通过快照和增量构建只读视图。

每个项目的数据保存在各自的 `.worklog/` 目录中。同号任务按项目区分。查询使用只读连接，写入由工具校验任务版本并处理安全重试。

## 安装和使用

需要 Python 3.9 或以上。可从 [v0.1.0 预发布](https://github.com/LaudasMe/worklog/releases/tag/v0.1.0) 下载安装包并核对 SHA-256，也可从源码构建。

```sh
python3 -B scripts/package_skill.py
```

按[安装说明](docs/skill.md)将 ZIP 解压到项目的 `.agents/skills/`。开启新会话后，在 Codex 使用 `$worklog`，在 OpenCode 使用 `@worklog`，再说明项目路径和任务编号。

- [用户指南](docs/user-guide.md)
- [安装与复跑说明](docs/skill.md)
- [CLI 调用与输入输出](docs/cli.md)
- [公开只读协议 1.0](docs/read-protocol.md)

## 验证

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

测试在临时目录生成合成项目，覆盖安装包重建、任务隔离、事务回滚、只读协议和跨目录接力。

许可证见 [LICENSE](LICENSE)。
