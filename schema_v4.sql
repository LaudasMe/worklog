CREATE TABLE tasks_upgraded (
    task_id TEXT PRIMARY KEY NOT NULL,
    number TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('TODO', 'IN_PROGRESS', 'PAUSED', 'DONE', 'CANCELLED')),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completion_json TEXT,
    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
    priority TEXT CHECK (priority IN ('p0', 'p1', 'p2', 'p3')),
    CHECK ((status = 'DONE') = (completion_json IS NOT NULL)),
    CHECK (archived = 0 OR status IN ('DONE', 'CANCELLED'))
);

INSERT INTO tasks_upgraded (task_id, number, title, status, revision, created_at, updated_at, completion_json)
SELECT task_id, number, title, status, revision, created_at, updated_at, completion_json FROM tasks ORDER BY rowid;

DROP TABLE tasks;
ALTER TABLE tasks_upgraded RENAME TO tasks;

ALTER TABLE changes ADD COLUMN task_v2_json TEXT;
ALTER TABLE changes ADD COLUMN visible_in_v1 INTEGER NOT NULL DEFAULT 1 CHECK (visible_in_v1 IN (0, 1));

CREATE TABLE protocol_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    v2_min_after INTEGER NOT NULL CHECK (v2_min_after >= 0)
);

INSERT INTO protocol_state (singleton, v2_min_after) SELECT 1, COALESCE(MAX(seq), 0) FROM changes;
