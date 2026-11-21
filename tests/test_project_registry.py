import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

from test_context import ContextCase, LEGACY, source_at
from worklog_store import connect, project_db, schema_version


class ProjectRegistryTest(ContextCase):
    @property
    def registry(self):
        return self.home / ".local" / "share" / "worklog" / "projects.json"

    def projects(self):
        return self.call("projects", "list", project=self.root)["result"]["projects"]

    def test_initialization_and_explicit_backfill_share_canonical_paths(self):
        self.assertEqual(self.projects(), [])
        self.assertFalse(self.home.exists())
        old_cli = source_at(LEGACY, self.root / "legacy")
        self.call("--project", str(self.project), "init", data={"name": "已有项目"}, cli=old_cli)
        identity = self.call("init", data={"name": "已有项目"})
        self.assertEqual(self.projects(), [])
        alias = self.root / "同一目录"
        alias.symlink_to(self.project, target_is_directory=True)
        for root in (alias, self.project):
            self.call("--project", str(root), "projects", "register")
        entry = {"path": str(self.project.resolve()), "project_id": identity["project_id"]}
        self.assertEqual(json.loads(self.registry.read_text()), {"projects": [entry]})
        self.assertEqual(self.projects(), [{**entry, "status": "available", "name": "已有项目"}])

        new_project = self.root / "新项目"
        new_project.mkdir()
        new_identity = self.call("init", project=new_project, data={"name": "新项目"})
        self.assertEqual({item["project_id"] for item in self.projects()},
                         {identity["project_id"], new_identity["project_id"]})

    def test_concurrent_processes_keep_both_registrations(self):
        paths = (self.project, self.root / "另一个项目")
        paths[1].mkdir()
        old_cli = source_at(LEGACY, self.root / "legacy")
        expected = {
            str(path.resolve()): self.call("--project", str(path), "init", project=path, data={"name": path.name},
                                         cli=old_cli)["project_id"]
            for path in paths
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            registrations = [pool.submit(self.call, "--project", str(path), "projects", "register")
                             for path in paths]
            for registration in registrations:
                registration.result()
        for path in paths:
            entries = self.call("projects", "list", project=path)["result"]["projects"]
            self.assertEqual({entry["path"]: entry["project_id"] for entry in entries}, expected)
            self.assertTrue(all(entry["status"] == "available" for entry in entries))

    def test_failed_automatic_registration_keeps_readable_database_for_retry(self):
        self.registry.parent.mkdir(parents=True)
        malformed = "不是 JSON\n"
        self.registry.write_text(malformed, encoding="utf-8")
        failure = self.call("init", data={"name": "初始化成功"}, error="project_registration_failed")
        details = failure["error"]["details"]
        self.assertEqual(details["path"], str(self.project.resolve()))
        self.assertEqual(self.registry.read_text(), malformed)
        identity = self.call("identity")
        self.assertEqual(identity["project_id"], details["project_id"])
        self.registry.unlink()
        self.call("--project", str(self.project), "projects", "register")
        self.assertEqual(self.projects()[0]["project_id"], identity["project_id"])

    def test_init_at_a_reused_path_explains_how_to_repair_registration(self):
        prior = self.call("init", data={"name": "原项目"})
        moved = self.root / "原项目新位置"
        self.project.rename(moved)
        self.project.mkdir()
        reply = self.call("init", data={"name": "新项目"}, error="project_registration_failed")
        self.assertIn("move or remove", reply["error"]["message"])
        current = self.call("identity")
        self.assertNotEqual(current["project_id"], prior["project_id"])
        self.call("projects", "register", error="project_mismatch")
        self.call("--project", str(moved), "projects", "move", "--from", str(self.project),
                  "--project-id", prior["project_id"])
        self.call("projects", "register")
        self.assertEqual({entry["project_id"] for entry in self.projects()},
                         {prior["project_id"], current["project_id"]})

    def test_move_verifies_identity_and_unregistration_leaves_history(self):
        identity = self.call("init", data={"name": "原项目"})
        project_id = identity["project_id"]
        self.write("task-create", "saved-task", identity, title="保留的旧任务")
        moved = self.root / "新目录"
        self.project.rename(moved)
        self.assertEqual(self.projects()[0]["status"], "unavailable")

        self.project.mkdir()
        old_cli = source_at(LEGACY, self.root / "legacy")
        wrong = self.call("--project", str(self.project), "init", data={"name": "错项目"}, cli=old_cli)
        listed = self.projects()[0]
        self.assertEqual((listed["status"], listed["actual_project_id"]),
                         ("identity_mismatch", wrong["project_id"]))
        self.call("--project", str(self.project), "projects", "register", error="project_mismatch")
        self.call("--project", str(self.project), "projects", "move", "--from", str(self.project),
                  "--project-id", project_id, error="project_mismatch")

        move = ("--project", str(moved), "projects", "move", "--from", str(self.project), "--project-id", project_id)
        self.assertEqual(self.call(*move), self.call(*move))
        self.assertEqual(self.projects()[0]["path"], str(moved.resolve()))
        self.call("--project", str(moved), "projects", "remove", "--project-id", wrong["project_id"],
                  error="project_mismatch")
        removed = self.call("--project", str(moved), "projects", "remove", "--project-id", project_id)["result"]
        self.assertTrue(removed["removed"])
        self.assertEqual(self.projects(), [])
        self.assertEqual(self.call("task-get", "T001", project=moved)["result"]["title"], "保留的旧任务")
        self.assertEqual(self.call("init", project=moved, data={"name": "原项目"}), identity)
        self.assertEqual(self.projects(), [])

    def test_unusable_symlink_stays_in_list_without_hiding_other_projects(self):
        identity = self.call("init", data={"name": "待移出的项目"})
        other = self.root / "可用项目"
        other.mkdir()
        self.call("init", project=other, data={"name": "可用项目"})
        self.project.rename(self.root / "移出的项目")
        self.project.symlink_to(self.project.name, target_is_directory=True)
        found = {row["path"]: row["status"] for row in self.projects()}
        loop = str(self.root.resolve() / self.project.name)
        self.assertEqual(found, {loop: "unavailable", str(other.resolve()): "available"})
        self.call("--project", loop, "projects", "register", project=self.root,
                  error="invalid_project_path")
        self.call("--project", loop, "projects", "move", "--from", loop,
                  "--project-id", identity["project_id"], project=self.root, error="invalid_project_path")
        self.call("--project", loop, "projects", "remove", "--project-id", identity["project_id"],
                  project=self.root)
        self.assertEqual(len(self.projects()), 1)

    def test_registry_selector_resolves_parent_after_traversing_symlink(self):
        wrong = self.root / "项目"
        wrong.mkdir()
        expected_parent = self.root / "外层"
        anchor = expected_parent / "内层"
        anchor.mkdir(parents=True)
        expected = expected_parent / wrong.name
        expected.mkdir()
        alias = self.root / "别名"
        alias.symlink_to(anchor, target_is_directory=True)
        self.call("init", project=wrong, data={"name": "同名项目一"})
        identity = self.call("init", project=expected, data={"name": "同名项目二"})
        path = alias / ".." / wrong.name
        reply = self.call("--project", str(path), "projects", "remove", "--project-id",
                          identity["project_id"])["result"]
        self.assertTrue(reply["removed"])
        self.assertEqual(reply["path"], str(expected.resolve()))
        self.assertEqual([row["path"] for row in self.projects()], [str(wrong.resolve())])

    def test_registry_rejects_escaped_surrogates_and_preserves_initialized_db(self):
        self.registry.parent.mkdir(parents=True)
        for field in ("path", "project_id"):
            with self.subTest(field=field):
                item = {"path": str(self.project.resolve()), "project_id": "sample"}
                item[field] += "\ud800"
                encoded = json.dumps({"projects": [item]}).encode("utf-8")
                self.registry.write_bytes(encoded)
                self.call("projects", "list", error="invalid_registry")
                self.assertEqual(self.registry.read_bytes(), encoded)
        failure = self.call("init", data={"name": "登记可重试"}, error="project_registration_failed")
        self.assertEqual(self.registry.read_bytes(), encoded)
        identity = self.call("identity")
        self.assertEqual(identity["project_id"], failure["error"]["details"]["project_id"])
        self.registry.write_text('{"projects":[]}', encoding="utf-8")
        self.call("projects", "register")
        self.assertEqual(self.projects()[0]["project_id"], identity["project_id"])

    def test_backup_and_upgrade_check_selected_identity(self):
        other = self.root / "另一个旧项目"
        other.mkdir()
        old_cli = source_at(LEGACY, self.root / "legacy")
        for project in (self.project, other):
            self.call("--project", str(project), "init", data={"name": project.name}, cli=old_cli)
            self.call("--project", str(project), "projects", "register")
        selected, unselected = [
            next(item for item in self.projects() if item["path"] == str(project.resolve()))
            for project in (self.project, other)
        ]
        other_db = project_db(other)
        original_other = other_db.read_bytes()
        destination = self.root / "selected-backup.sqlite3"
        backup = ("--project", selected["path"], "backup", "--output", str(destination), "--project-id")
        upgrade = ("--project", selected["path"], "upgrade", "--backup", str(destination), "--project-id")
        self.call(*backup, unselected["project_id"], error="project_mismatch")
        self.assertFalse(destination.exists())
        self.assertEqual(self.call(*backup, selected["project_id"])["result"]["schema_version"], 1)
        self.call(*upgrade, unselected["project_id"], error="project_mismatch")
        with closing(connect(self.db, "ro")) as db:
            self.assertEqual(schema_version(db), 1)
        self.assertEqual(self.call(*upgrade, selected["project_id"])["result"]["schema_version"], 4)
        self.assertEqual(other_db.read_bytes(), original_other)
        self.assertEqual({item["project_id"] for item in self.projects()},
                         {selected["project_id"], unselected["project_id"]})
