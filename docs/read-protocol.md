# 只读接口说明 1.0

用下面的读取命令，可以把任务和进展显示在自己的工具里。命令的完整写法是 `python3 worklog.py --project <项目根目录> <命令>`。这些命令只读取已有数据库。项目尚未初始化时会返回 `not_initialized`。

| 命令 | 读到什么 |
| --- | --- |
| `identity` | 项目名称和支持的读取命令。 |
| `task-get T001` | 一个任务的状态、最近记录和下一步。也可以传任务 ID。 |
| `snapshot` | 项目的任务列表、工作记录的摘要和 ID，以及读取时已保存的最后一个变更序号。 |
| `changes --project-id ID --data-generation GEN --after SEQ [--limit N]` | 从指定序号之后开始读变更。每次默认返回最多 100 条，可设为 1 到 500。 |
| `entry-get <entry_id>` | 一条工作记录的摘要、时间和 Markdown 原文。 |

每条成功结果都是一行 UTF-8 JSON。用 `ok` 判断是否成功。数据放在 `result`，同一行还有 `protocol_version`、`project_id` 和 `data_generation`。错误格式见 [CLI 说明](cli.md)。

## 确认读的是同一个项目

- `project_id` 是 workLog 为项目分配的固定 ID。项目目录连同数据库一起移动后，ID 仍相同。
- `data_generation` 是这份项目数据的标记。保存已读到的位置时，连同 `project_id` 和这个标记一起保存。如果标记不同，确认来源后重新读一次完整列表。v0.1.0 支持生成和校验这个标记。
- `task_id` 是任务自己的 ID。界面可以显示 `number`，例如 T001。两个项目都可能有 T001，定位任务时应同时使用项目 ID 和任务 ID。

调用 `changes` 时要传项目 ID 和数据标记。其他读取命令也会返回这两个值，程序可用它们确认读到了正确的数据。先核对 `protocol_version` 是否是程序支持的版本。数据库内部格式另有自己的版本号。

## 从上次读到的位置继续

假设你第一次读取时，workLog 已经保存了编号 1 到 4 的变更。之后又有人接手任务、记了一条进展，这两次操作的序号是 5 和 6。按下面的步骤读取。

1. **先拿当前列表。** 调用 `snapshot`，`result.tasks` 是任务列表，`result.entry_refs` 列出工作记录的摘要和 ID，`result.watermark` 是这份列表已包含的最后一个变更序号。在例子里是 4。三项来自同一次读取。在这之后保存的新变更，可用这个序号继续读。项目没有变更时，`watermark` 为 0。
2. **只读后面的新变更。** 用刚拿到的两个项目标记调用 `changes --project-id ID --data-generation GEN --after 4`。它会返回序号大于 4 的变更，按 `seq` 从小到大排列。序号可能有间隔。
3. **需要分批时，从结果给出的位置接着读。** 如果设置 `--limit 1`，本例第一次会读到 5。结果里的 `next_after` 是 5，下一次就传 `--after 5`。若返回空列表，`next_after` 仍等于原来的 `--after`。

`changes` 的 `result.changes` 是本次读到的变更。`result.watermark` 是**本次读取时，整个项目已经保存的最后一个变更序号**，所以可能比这一批的 `next_after` 大。`has_more` 表示在这次读取时后面还有数据。之后有新进展时，可以从上次的 `next_after` 继续读取。若 `--after` 大于项目当前的最后一个序号，会返回 `cursor_ahead`。

每次新保存的操作产生一条变更。任务更新与变更记录会一起保存。每条有 `seq`、`change_id`、操作名 `kind`、保存时间 `committed_at`、保存后的任务 `task`，以及新记录的摘要和 ID 等信息 `entry`。本次没有新增记录时，`entry` 为 `null`。重复提交同一次已保存的请求会返回原结果。读取命令不会产生新的变更。

如果需要在自己的程序里保存一份任务列表，可以按 `(project_id, data_generation, change_id)` 识别已处理过的变更，再用 `task_id` 更新对应任务，用 `entry_id` 合并工作记录。重复读取时，结合 `revision` 避免旧状态覆盖新状态。把这一批保存成功之后，再记录下一次从哪儿开始读。如果先缓存、稍后再处理，可以分别记下已保存和已应用到的变更序号。

## 任务和记录的主要字段

- **任务**包含 `task_id`、显示编号 `number`、标题 `title`、状态 `status`、版本 `revision`、创建和更新时间 `created_at`、`updated_at`，以及 `active_run`、`latest_entry`、`latest_handoff` 和 `next_step`。任务状态有 `TODO` 和 `IN_PROGRESS`。任务每次有新的写入，版本号就会更新。
- **当前接手**是 `active_run`。有接手时包含 `run_id`、`client`、`session_id` 和 `opened_at`，否则是 `null`。交接会结束接手，任务仍显示为进行中。
- **工作记录的概览**包含 `entry_id`、`task_id`、`run_id`、`client`、`session_id`、`kind`、`summary`、`next_step` 和 `recorded_at`。类型 `PROGRESS` 表示进展，`HANDOFF` 表示交接。用 `entry-get` 取回 `body_md` 原文。最近一条记录在 `latest_entry`，最近一次交接在 `latest_handoff`。

时间字段使用 UTC 的 ISO 8601 格式。排序时使用变更序号。

## 自己生成上面的例子

按 [CLI 的示例步骤](cli.md#在两个目录运行示例)执行 `start`，然后运行 `snapshot`。接着执行 `resume`，再用 `changes` 读快照序号之后的新变更。得到记录 ID 后，可用 `entry-get` 查看原文。
