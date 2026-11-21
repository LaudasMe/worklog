"""Explicit consistent backups and transactional schema upgrades."""

from contextlib import closing
import os
from pathlib import Path
import tempfile

from worklog_store import (
    CONTEXT_SCHEMA_VERSION, LEGACY_SCHEMA_VERSION, SCHEMA_VERSION, WorklogError,
    connect, metadata, schema_version,
)


def backup(path, destination):
    output = Path(destination).expanduser().resolve()
    if output.exists():
        raise WorklogError("backup_exists", "Backup destination already exists.")
    fd, name = tempfile.mkstemp(prefix=".worklog-backup-", dir=output.parent)
    os.close(fd)
    temp = Path(name)
    try:
        with closing(connect(path, "ro")) as source, closing(connect(temp, "rw")) as copy:
            source.execute("BEGIN")
            project = metadata(source)
            version = schema_version(source)
            source.backup(copy)
            if metadata(copy) != project or schema_version(copy) != version:
                raise WorklogError("invalid_backup", "Backup identity or schema differs.")
            check_integrity(copy)
        try:
            os.link(temp, output)
        except FileExistsError as exc:
            raise WorklogError("backup_exists", "Backup destination already exists.") from exc
    finally:
        temp.unlink()
    return project, {"backup_path": str(output), "schema_version": version}


def check_integrity(db):
    if [row[0] for row in db.execute("PRAGMA integrity_check")] != ["ok"] or db.execute(
        "PRAGMA foreign_key_check"
    ).fetchone():
        raise WorklogError("invalid_backup", "Database failed integrity or foreign key checks.")


def install_schema(db, filename, version):
    # executescript commits a pending transaction before running its script.
    schema = Path(__file__).with_name(filename).read_text(encoding="utf-8")
    for statement in schema.split(";"):
        if statement.strip():
            db.execute(statement)
    db.execute(f"PRAGMA user_version = {version}")


def install_context_schema(db):
    install_schema(db, "schema_context.sql", CONTEXT_SCHEMA_VERSION)


def install_lifecycle_schema(db):
    install_schema(db, "schema_lifecycle.sql", SCHEMA_VERSION)


def upgrade(path, backup_name):
    copy_path = Path(backup_name).expanduser().resolve()
    if not copy_path.is_file() or copy_path.samefile(path):
        raise WorklogError("invalid_backup", "Supply a separate existing SQLite backup.")
    with closing(connect(path, "rw")) as db, db:
        # The tasks table is rebuilt with the same keys. Check all foreign keys before commit.
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("BEGIN IMMEDIATE")
        project = metadata(db)
        version = schema_version(db)
        if version == SCHEMA_VERSION:
            raise WorklogError("already_upgraded", "Database is already at the current schema.")
        with closing(connect(copy_path, "ro")) as copy, copy:
            copy.execute("BEGIN")
            if schema_version(copy) != version or metadata(copy) != project:
                raise WorklogError("invalid_backup", "Backup must have the original identity and schema.")
            check_integrity(copy)
            # Compare logical content, including saved receipts. A stale backup cannot protect newer writes.
            if list(copy.iterdump()) != list(db.iterdump()):
                raise WorklogError("invalid_backup", "Backup differs from the current database. Back up again.")
        if version == LEGACY_SCHEMA_VERSION:
            install_context_schema(db)
        install_lifecycle_schema(db)
        check_integrity(db)
    return project, {"previous_schema_version": version, "schema_version": SCHEMA_VERSION}
