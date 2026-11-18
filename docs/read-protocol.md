# workLog 公开只读协议 1.0

本地进程调用 `python3 <workLog>/worklog.py --project <项目根> <命令>`。每次调用产生一条 UTF-8 JSON。成功结果包含 `ok: true`、`protocol_version: "1.0"`、`project_id`、`data_generation` 和 `result`。失败格式和退出码见 [CLI](cli.md)。只读命令以 SQLite 只读方式连接已初始化项目。未初始化目录返回 `not_initialized`，目录保持原样。

| 命令 | `result` |
| --- | --- |
| `identity` | `name` 和本实例的 `capabilities` |
| `task-get <task_id 或 T001>` | 单一时点的任务及最新记录、交接引用和下一步 |
| `snapshot` | `watermark`、`tasks`、`entry_refs` |
| `changes --project-id ID --data-generation GEN --after SEQ [--limit N]` | `changes`、`next_after`、`has_more`、`watermark`。默认上限 100 批，可传 1 至 500 |
| `entry-get <entry_id>` | 记录元数据和原始 `body_md` |

## 身份

`project_id` 在初始化后持久化，目录整体移动后保持相同。任务以 `project_id` 与 `task_id` 识别，`number` 供本项目展示。对于当前数据流，游标按 `project_id` 与 `data_generation` 保存。两者都是不透明字符串。`changes` 校验调用方提供的登记项目和代次，其余只读响应由调用方校验返回的身份。发现身份不符应暂停应用。遇到新代次时，连接器需经重新核对后按新快照重建来源镜像。PoC 暂只生成和校验初始代次，受控恢复及变更代次属于后续验证。

公开协议版本与 SQLite 内部结构版本分别管理。PoC 对不识别的 SQLite 结构拒绝读写。消费者应识别其支持的协议版本和能力后应用数据。

## 任务和记录

- 任务含 `task_id`、`number`、`title`、`status`、`revision`、`created_at`、`updated_at`、`active_run`、`latest_entry`、`latest_handoff`、`next_step`。状态支持 `TODO` 和 `IN_PROGRESS`。每次成功的业务变更推进任务版本。接手可能关闭而任务仍在进行中。
- `active_run` 为 `null` 或含 `run_id`、`client`、`session_id`、`opened_at` 的对象。它表示登记的接手归属。界面据此显示接手人，运行在线状态需要额外来源。
- `entry_refs` 或任务上的记录引用含 `entry_id`、`task_id`、`run_id`、`client`、`session_id`、`kind`、`summary`、`next_step` 和 `recorded_at`。`kind` 为 `PROGRESS` 或 `HANDOFF`。引用以 `entry_id` 去重，完整正文用 `entry-get` 取回。
- `latest_handoff` 保留最近一次已保存的交接。产生后续进展时，`latest_entry` 和任务 `next_step` 指向新进展。时间字段为 UTC ISO 8601。增量的 `committed_at` 是变化批次在写事务中登记的时间，排序采用 `seq`。

## 快照后接增量

1. 从一次 `snapshot` 保存项目身份、代次、任务、记录引用和 `watermark`。这些值来自同一个读事务，快照之后提交的变化可从水位后读取。空库的水位为 0。
2. 以快照水位为排他游标调用 `changes`。同一响应里的批次按 `seq` 升序排列。每批包括唯一 `change_id`、写入类型、提交后的完整任务投影和本次新增的 `entry` 引用（没有记录时为 `null`）。分页序号可有间隔。
3. 处理每页并以 `next_after` 请求下一页。它等于本页最后一批的序号，空页保留原游标。`watermark` 是本次分页读事务的最高已提交序号。`has_more` 是本次读事务观察到的剩余状态，之后有新写入时仍可继续拉取。游标超前返回 `cursor_ahead`。
4. 以 `(project_id, data_generation, change_id)` 去重。按顺序将任务投影应用到 `task_id`，按 `entry_id` 合并引用。若另设补偿重放，应同时检查 `revision`，防止旧投影覆盖新投影。消费端的持久接收位置在页面可靠保存后推进，应用位置在本地投影成功后推进。

每个成功业务请求和其变化、操作指纹和回执由同一 SQLite 事务提交。重放成功的写请求返回同一批次。只读查询不增加变化。全量快照和历史引用在 v1.0 一次返回。随历史规模扩大，应新增并验收快照的分页或分段能力。

## 用合成项目生成样本

先按 [CLI 中的复跑步骤](cli.md#从不同目录复跑)运行 `start`，再执行 `snapshot`。记录返回的项目 ID、数据代次与水位。运行 `resume` 后，从该水位调用 `changes`，便可读到后续的接手和进展。对增量中带有记录引用的批次，使用同项目的 `entry-get` 查询原文。
