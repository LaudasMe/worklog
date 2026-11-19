# workLog

workLog 帮你在 Codex 和 OpenCode 之间接着做同一个项目。你可以在 Codex 记下做到哪里，之后到 OpenCode 读回进展和下一步，再接手同一条任务。

你可以新建任务、查看进展、接手、记录 Markdown 原文，也可以把任务交给下一个会话。每个项目各有一个数据库，放在自己的 `.worklog/` 目录里。

## 第一次使用

1. 准备 Python 3.9 或以上。有安装 ZIP 就直接解压，也可以从 [v0.1.0 源码](https://github.com/LaudasMe/worklog/tree/v0.1.0) 运行 `python3 -B scripts/package_skill.py` 生成 ZIP。
2. 按[安装说明](docs/skill.md)把 ZIP 解压到客户端能找到的位置。
3. 开一个新会话。Codex 用 `$worklog`，OpenCode 用 `@worklog`。告诉它项目路径和你要做的事。

[用户指南](docs/user-guide.md)有完整例子，从创建 T001 到换一个客户端继续处理。

## 从命令行使用

[CLI 说明](docs/cli.md)提供可运行的命令和常见错误的处理方法。[只读接口说明](docs/read-protocol.md)适合要在其他工具里显示任务和进展的人。

## 运行测试

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

测试用临时项目检查安装包、跨目录接力、项目之间的数据隔离和只读查询。

许可证见 [LICENSE](LICENSE)。
