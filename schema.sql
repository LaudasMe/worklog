CREATE TABLE project (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    project_id TEXT NOT NULL UNIQUE,
    data_generation TEXT NOT NULL,
    name TEXT NOT NULL
);

CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY NOT NULL,
    number TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('TODO', 'IN_PROGRESS')),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE client_sessions (
    client TEXT NOT NULL,
    session_id TEXT NOT NULL,
    PRIMARY KEY (client, session_id)
);

CREATE TABLE task_runs (
    run_id TEXT PRIMARY KEY NOT NULL,
    task_id TEXT NOT NULL REFERENCES tasks (task_id),
    client TEXT NOT NULL,
    session_id TEXT NOT NULL,
    working_directory TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('OPEN', 'CLOSED', 'SUPERSEDED')),
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    FOREIGN KEY (client, session_id) REFERENCES client_sessions (client, session_id),
    CHECK ((state = 'OPEN') = (closed_at IS NULL))
);
CREATE UNIQUE INDEX one_open_run ON task_runs (task_id) WHERE state = 'OPEN';

CREATE TABLE entries (
    position INTEGER PRIMARY KEY,
    entry_id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL REFERENCES tasks (task_id),
    run_id TEXT NOT NULL REFERENCES task_runs (run_id),
    kind TEXT NOT NULL CHECK (kind IN ('PROGRESS', 'HANDOFF')),
    summary TEXT NOT NULL,
    body_md TEXT NOT NULL,
    next_step TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX entries_by_task ON entries (task_id, position);

CREATE TABLE changes (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    change_id TEXT NOT NULL UNIQUE,
    operation_id TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    payload TEXT NOT NULL
);
