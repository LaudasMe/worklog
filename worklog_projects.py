"""The per-user project registry shared by installed workLog Skills."""

from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile

from worklog_store import WorklogError, connect, json_text, metadata, project_db, require_project_id


def registry_path():
    return Path.home() / ".local" / "share" / "worklog" / "projects.json"


def read_registry(path):
    try:
        contents = json.loads(path.read_text(encoding="utf-8"))
        json_text(contents).encode("utf-8")
    except FileNotFoundError:
        return {}
    except (ValueError, RecursionError) as exc:
        raise WorklogError("invalid_registry", "The project registry must contain valid UTF-8 JSON.", path=str(path)) from exc
    if not isinstance(contents, dict) or not isinstance(contents.get("projects"), list):
        raise WorklogError("invalid_registry", "Check the project registry format.", path=str(path))
    projects = {}
    for entry in contents["projects"]:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("path"), str)
            or not os.path.isabs(entry["path"])
            or "\0" in entry["path"]
            or not isinstance(entry.get("project_id"), str)
            or not entry["project_id"]
            or entry["path"] in projects
        ):
            raise WorklogError("invalid_registry", "Check the project registry entries.", path=str(path))
        projects[entry["path"]] = {"path": entry["path"], "project_id": entry["project_id"]}
    return projects


@contextmanager
def locked_registry():
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Lock a separate file so replacing the JSON cannot give another writer a different lock.
    fd = os.open(path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "r+b") as lock:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        yield path, read_registry(path)


def write_registry(path, projects):
    file = NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".projects-", delete=False)
    temporary = Path(file.name)
    try:
        with file:
            file.write(json_text({"projects": [projects[key] for key in sorted(projects)]}) + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def unresolved_project(root):
    return WorklogError("invalid_project_path", "Cannot resolve the project path.", path=str(root))


def canonical_root(root):
    try:
        return str(Path(root).expanduser().resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise unresolved_project(root) from exc


def registered_root(root, projects):
    # Make the path absolute without collapsing ".." before traversing symlinks.
    given = os.path.expanduser(root)
    absolute = given if os.path.isabs(given) else os.path.join(os.getcwd(), given)
    # An exact stored path can still be removed if it is no longer usable.
    return absolute if absolute in projects else canonical_root(root)


def read_identity(root):
    try:
        path = project_db(root)
    except (RuntimeError, ValueError) as exc:
        raise unresolved_project(root) from exc
    if not path.is_file():
        raise WorklogError("not_initialized", "No workLog database at the supplied project location.")
    with closing(connect(path, "ro")) as db:
        return metadata(db)


def list_projects():
    path = registry_path()
    projects = []
    for entry in sorted(read_registry(path).values(), key=lambda item: item["path"]):
        try:
            identity = read_identity(entry["path"])
        except (OSError, sqlite3.Error, WorklogError):
            projects.append({**entry, "status": "unavailable"})
        else:
            if identity["project_id"] == entry["project_id"]:
                projects.append({**entry, "status": "available", "name": identity["name"]})
            else:
                projects.append({**entry, "status": "identity_mismatch", "actual_project_id": identity["project_id"]})
    return {"registry_path": str(path), "projects": projects}


def register_project(root):
    root = canonical_root(root)
    identity = read_identity(root)
    return register_identity(root, identity)


def register_identity(root, identity):
    root = canonical_root(root)
    entry = {"path": root, "project_id": identity["project_id"]}
    with locked_registry() as (path, projects):
        existing = projects.get(root)
        if existing is not None:
            require_project_id(entry, existing["project_id"])
        else:
            projects[root] = entry
            write_registry(path, projects)
    return entry


def register_new_project(root, identity):
    try:
        register_identity(root, identity)
    except (OSError, WorklogError) as exc:
        next_step = "Use projects register with the same project path to retry registration."
        if isinstance(exc, WorklogError) and exc.code == "project_mismatch":
            next_step = "Check projects list and explicitly move or remove the previous registration before retrying."
        raise WorklogError(
            "project_registration_failed", f"Database initialized. {next_step}",
            path=str(root), project_id=identity["project_id"], data_generation=identity["data_generation"],
            reason=str(exc),
        ) from exc


def move_project(old_root, new_root, expected_id):
    new_root = canonical_root(new_root)
    identity = read_identity(new_root)
    require_project_id(identity, expected_id)
    entry = {"path": new_root, "project_id": expected_id}
    with locked_registry() as (path, projects):
        old_root = registered_root(old_root, projects)
        old = projects.get(old_root)
        current = projects.get(new_root)
        if current is not None:
            require_project_id(current, expected_id)
        if old is None:
            if current is not None:
                return current
            raise WorklogError("project_not_registered", "The previous path is not registered.", path=old_root)
        require_project_id(old, expected_id)
        if old_root != new_root:
            del projects[old_root]
            projects[new_root] = entry
            write_registry(path, projects)
    return entry


def remove_project(root, expected_id):
    with locked_registry() as (path, projects):
        root = registered_root(root, projects)
        entry = projects.get(root)
        if entry is not None:
            require_project_id(entry, expected_id)
            del projects[root]
            write_registry(path, projects)
    return {"path": root, "project_id": expected_id, "removed": entry is not None}
