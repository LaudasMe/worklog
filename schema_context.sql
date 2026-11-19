CREATE TABLE context_records (
    position INTEGER PRIMARY KEY,
    record_id TEXT NOT NULL UNIQUE,
    scope TEXT NOT NULL CHECK (scope IN ('PROJECT', 'TASK')),
    task_id TEXT REFERENCES tasks (task_id),
    related_task_id TEXT REFERENCES tasks (task_id),
    kind TEXT NOT NULL CHECK (kind IN (
        'GOAL', 'CONSTRAINT', 'DECISION', 'DOCUMENT', 'PROGRESS', 'QUESTION', 'TODO', 'BLOCKER'
    )),
    summary TEXT NOT NULL,
    body_md TEXT NOT NULL,
    details_json TEXT NOT NULL,
    action_status TEXT CHECK (action_status IN ('OPEN', 'RESOLVED', 'CANCELLED')),
    replaces_id TEXT UNIQUE REFERENCES context_records (record_id),
    recorded_at TEXT NOT NULL,
    client TEXT,
    session_id TEXT,
    run_id TEXT REFERENCES task_runs (run_id),
    CHECK ((action_status IS NOT NULL) = (kind IN ('QUESTION', 'TODO', 'BLOCKER'))),
    CHECK ((scope = 'TASK' AND task_id IS NOT NULL AND related_task_id IS NULL)
        OR (scope = 'PROJECT' AND task_id IS NULL))
);
CREATE INDEX context_by_task ON context_records (task_id, position);
CREATE INDEX context_by_related_task ON context_records (related_task_id, position);

CREATE TABLE context_record_refs (
    record_id TEXT NOT NULL REFERENCES context_records (record_id),
    ordinal INTEGER NOT NULL,
    reference_json TEXT NOT NULL,
    PRIMARY KEY (record_id, ordinal)
);

CREATE TABLE context_operations (
    operation_id TEXT PRIMARY KEY NOT NULL,
    request_hash TEXT NOT NULL,
    receipt TEXT NOT NULL
);

CREATE VIEW context_index AS
SELECT r.*, successor.record_id AS superseded_by,
    CASE WHEN successor.record_id IS NOT NULL THEN 'SUPERSEDED'
        ELSE COALESCE(r.action_status, 'CURRENT')
    END AS status
FROM context_records r
LEFT JOIN context_records successor ON successor.replaces_id = r.record_id;
