CREATE TABLE tasks_upgraded (
    task_id TEXT PRIMARY KEY NOT NULL,
    number TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('TODO', 'IN_PROGRESS', 'DONE')),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completion_json TEXT,
    CHECK ((status = 'DONE') = (completion_json IS NOT NULL))
);

INSERT INTO tasks_upgraded (task_id, number, title, status, revision, created_at, updated_at)
SELECT task_id, number, title, status, revision, created_at, updated_at FROM tasks ORDER BY rowid;

DROP TABLE tasks;
ALTER TABLE tasks_upgraded RENAME TO tasks;
