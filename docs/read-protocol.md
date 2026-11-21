# workLog 公开只读协议 2.0

公开只读命令默认使用 2.0，也可在子命令前显式传入 `--protocol-version 2.0`。请求 1.0 会返回 `invalid_input`。以下示例用 `$WORKLOG_CLI` 指向 `worklog.py`，用 `$WORKLOG_PROJECT` 指向已选定的项目目录。

```sh
python3 "$WORKLOG_CLI" --project "$WORKLOG_PROJECT" identity
python3 "$WORKLOG_CLI" --project "$WORKLOG_PROJECT" --protocol-version 2.0 identity
```

每次调用产生一条 UTF-8 JSON。成功响应包含 `ok: true`、`protocol_version: "2.0"`、`project_id`、`data_generation` 和 `result`。公开只读命令以 SQLite 只读方式连接已初始化项目。未初始化目录返回 `not_initialized`，目录保持原样。

公开读取要求 schema 4。schema 1 至 3 返回 `upgrade_required`，可以先用本地 `context` 和 `summaries` 核对数据，再按备份和升级流程处理。协议参数只适用于下表的公开只读命令。本地命令直接调用。 [旧库备份和升级](context-cli.md#备份和升级) · [错误响应和退出码](cli.md#输入和回执)

| 命令 | `result` |
| --- | --- |
| `identity` | `name` 和本实例的 `capabilities` |
| `task-get <task_id 或 T001>` | 单一时点的任务及最新记录、交接引用和下一步 |
| `snapshot` | `watermark`、`tasks`、`entry_refs` |
| `changes --project-id ID --data-generation GEN --after SEQ [--limit N]` | 变更记录（`changes`）、本页读到的最后编号（`next_after`）、是否还有后续记录（`has_more`）和当前最新编号（`watermark`）。每页默认最多 100 条，可传 1 至 500 |
| `entry-get <entry_id>` | 记录元数据和原始 `body_md` |

## 身份

`project_id` 在初始化后持久化，目录整体移动后保持相同。任务以 `project_id` 与 `task_id` 识别，`number` 供本项目展示。项目 ID 和数据代次都是不透明字符串。`changes` 校验调用方提供的项目 ID 与数据代次，分别在不符时返回 `project_mismatch` 或 `generation_mismatch`。调用方对每次成功响应核对版本、项目 ID 和数据代次。发现身份不符应暂停应用。遇到新代次时，连接器需经重新核对后按新快照重建来源镜像。

调用方按 `(protocol_version, project_id, data_generation)` 分别保存上次读到的编号和已应用到本地的编号。曾使用 1.0 的调用方从 2.0 快照的当前最新编号（`watermark`）建立同步起点。已有对应项目和代次的 2.0 读取位置可以继续使用。恢复旧备份后，从新快照重建读取位置。恢复后的项目 ID 和数据代次沿用备份中的值。

## 任务和记录

- 任务包含 `task_id`、`number`、`title`、`status`、`revision`、`created_at`、`updated_at`、`active_run`、`latest_entry`、`latest_handoff`、`next_step`。任务写入推进任务修订。仅改变生效阻塞的上下文写入沿用原任务修订和更新时间。
- `active_run` 为 `null` 或含 `run_id`、`client`、`session_id`、`opened_at` 的对象。它表示登记的接手归属。界面据此显示接手人，运行在线状态需要额外来源。
- `entry_refs` 或任务上的记录引用含 `entry_id`、`task_id`、`run_id`、`client`、`session_id`、`kind`、`summary`、`next_step` 和 `recorded_at`。`kind` 为 `PROGRESS` 或 `HANDOFF`。引用以 `entry_id` 去重，完整正文用 `entry-get` 取回。
- `latest_handoff` 保留最近一次已保存的交接。产生后续进展时，`latest_entry` 和任务 `next_step` 指向新进展。时间字段为 UTC ISO 8601。增量的 `committed_at` 是变化批次在写事务中登记的时间，排序采用 `seq`。
- 基础状态 `status` 支持 `TODO`、`IN_PROGRESS`、`PAUSED`、`DONE` 和 `CANCELLED`。待开始或进行中的任务有生效阻塞时，`display_status` 为 `BLOCKED`。其他情况下它沿用基础状态。`archived` 为布尔值。未设置或清空时 `priority` 为 `null`，设置时为 `p0` 至 `p3`。`blocker_refs` 列出生效阻塞，每项是含 `command: "record-get"` 和原记录 `id` 的引用。完成的任务附带 `completion`。生命周期操作历史及原因可用本地 `context --task` 查询。
- `record-get` 引用指向本地记录。核对原文时直接调用 `record-get <id>`，省略协议参数。

## 快照后接增量

当前最新编号（`watermark`）是库中变更记录的最大编号，包含生效阻塞引用的变化。没有变更记录时，当前最新编号为 0。

1. 从一次 `snapshot` 保存项目身份、代次、任务、记录引用和当前最新编号（`watermark`）。这些值来自同一个读事务。完整快照包含全部任务，包括已归档的任务，以及全部进展和交接的 `entry_refs`。
2. 首次调用 `changes` 时，将快照的当前最新编号传给 `--after`。分页使用与当前最新编号相同的变更范围，取上次读到的编号之后的变更记录，按 `seq` 升序返回。每条变更记录包含 `seq`、唯一的 `change_id`、事件类型 `kind`、`committed_at`、提交时的 `task` 投影和本次新增的 `entry` 引用（没有记录时为 `null`）。分页序号可有间隔。
3. 处理每页后用返回的编号（`next_after`）作为下一次的 `--after`。它等于本页最后一批的编号，空页沿用上次读到的编号。若 `next_after` 小于本次读事务的当前最新编号（`watermark`），则 `has_more` 为 `true`。调用方传入的编号超过该版本的当前最新编号时，返回 `cursor_ahead`。
4. 在当前协议、项目 ID 和数据代次内按 `change_id` 去重。按 `seq` 应用各事件的任务投影，按 `entry_id` 合并引用。`task-blockers` 事件在生效阻塞引用改变时写入。两条事件即使任务修订相同，也要按 `seq` 分别处理。页面可靠保存后才更新上次读到的编号，成功应用本地投影后才更新已应用的编号。读到错误响应时保留这两个编号。

新库可以从 0 续读。升级后的库以升级前的最大变更编号作为最早续读位置。请求 `after` 早于该位置时返回 `cursor_stale`，错误详情 `min_after` 给出最早续读位置，调用方需重新读取快照。快照包含迁移前已有的任务和记录引用。

需对外发布的变更与相应业务写入、操作指纹和回执在同一个 SQLite 事务提交。重试同一个已成功的写请求时，`result` 重放原始写回执。可通过 `seq` 和 `change_id` 核对对应的公开事件，公开事件的 `task` 包含显示状态、归档、优先级和生效阻塞等字段。

## 快照和分页示例

```sh
python3 "$WORKLOG_CLI" --project "$WORKLOG_PROJECT" snapshot
python3 "$WORKLOG_CLI" --project "$WORKLOG_PROJECT" --protocol-version 2.0 snapshot
```

以下合成快照中，第 11 条变更新增了一个生效阻塞。任务的基础状态为 `IN_PROGRESS`，显示状态为 `BLOCKED`。

```json
{
  "ok": true,
  "protocol_version": "2.0",
  "project_id": "demo-project",
  "data_generation": "demo-generation",
  "result": {
    "watermark": 11,
    "tasks": [
      {
        "task_id": "demo-task",
        "number": "T001",
        "title": "复核交付材料",
        "status": "IN_PROGRESS",
        "revision": 3,
        "created_at": "2026-11-21T02:00:00+00:00",
        "updated_at": "2026-11-21T02:05:00+00:00",
        "active_run": null,
        "latest_entry": null,
        "latest_handoff": null,
        "next_step": null,
        "display_status": "BLOCKED",
        "archived": false,
        "priority": "p2",
        "blocker_refs": [{"command": "record-get", "id": "demo-blocker"}]
      }
    ],
    "entry_refs": []
  }
}
```

把快照的 `watermark` 传给 `--after`。下面的项目 ID 和数据代次应替换为实际响应中的值。

```sh
python3 "$WORKLOG_CLI" --project "$WORKLOG_PROJECT" --protocol-version 2.0 changes \
  --project-id demo-project --data-generation demo-generation --after 11 --limit 1
```

如果当前最新编号仍为 11，返回的 `result` 为 `{"changes":[],"next_after":11,"has_more":false,"watermark":11}`。如果第 12 条修改了优先级且是当前最后一条变更，`next_after` 和 `watermark` 都是 12，`has_more` 为 `false`。调用方保存该页后更新上次读到的编号，按事件的 `task` 更新本地投影后再更新已应用编号。

## 合成样本

可用 `examples/relay.py` 复跑当前协议下的写入、接力、快照和增量。 [复跑步骤](cli.md#从不同目录复跑) · [合成请求正文](../examples/sample.json)

## 从协议 1.0 切换

先让读取方使用 2.0，并从新的 2.0 快照开始。把快照的当前最新编号（`watermark`）作为增量起点。旧库应先按项目备份和升级到 schema 4。 [旧库备份和升级](context-cli.md#备份和升级)
