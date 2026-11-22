import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from worklog_release import RELEASES_API, RELEASES_PAGE, check_update, parse_version


def release(tag, *, draft=False, prerelease=False):
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease}


def response(releases, links=None):
    stream = io.BytesIO(json.dumps(releases).encode())
    stream.headers = {} if links is None else {"Link": links}
    return stream


class ReleaseCheckTest(unittest.TestCase):
    def test_components_ignore_other_tags_drafts_and_prereleases(self):
        releases = [
            release("v10.0.0", draft=True),
            release("v12.0.0", prerelease=True),
            release("v9.0.0-rc.1"),
            release("other-v9.0.0"),
            release("v1.2.0"),
            release("gui-codex-v0.1.1"),
            release("gui-opencode-v9.0.0"),
            release("gui-codex-v1.0.0", prerelease=True),
            release("gui-codex-v2.0.0", draft=True),
        ]
        with patch("worklog_release.github_releases", return_value=releases):
            for installed, status in [
                ("v1.1.1", "update_available"), ("v1.2.0", "up_to_date"),
                ("v1.10.0", "ahead"), (None, "unknown"),
            ]:
                with self.subTest(installed=installed):
                    self.assertEqual(check_update("cli", installed), {
                        "installed_version": installed,
                        "latest_version": "v1.2.0",
                        "status": status,
                        "release_url": f"{RELEASES_PAGE}/tag/v1.2.0",
                        "error": None,
                    })
            self.assertEqual(check_update("codex", "gui-codex-v0.1.0"), {
                "installed_version": "gui-codex-v0.1.0",
                "latest_version": "gui-codex-v0.1.1",
                "status": "update_available",
                "release_url": f"{RELEASES_PAGE}/tag/gui-codex-v0.1.1",
                "error": None,
            })

    def test_checks_all_release_pages(self):
        first = response([release("v1.2.0")], '<https://api.github.com/?page=2>; rel="next"')
        second = response([release("v1.10.0")])
        with patch("worklog_release.urlopen", side_effect=[first, second]) as fetch:
            latest = check_update("cli", "v1.2.0")
        self.assertEqual(latest["latest_version"], "v1.10.0")
        self.assertEqual([call.args[0].full_url for call in fetch.call_args_list], [
            f"{RELEASES_API}?per_page=100&page=1", f"{RELEASES_API}?per_page=100&page=2",
        ])

    def test_remote_errors_preserve_local_version_and_retry(self):
        unavailable = HTTPError(RELEASES_API, 503, "Service unavailable", {}, None)
        with patch("worklog_release.urlopen", side_effect=[
            URLError("offline"), unavailable, response({}), response([release("v0.4.0")]),
        ]):
            for reason in ["offline", "HTTP 503", "无法读取"]:
                with self.subTest(reason=reason):
                    failed = check_update("cli", "v0.4.0")
                    self.assertEqual(failed["installed_version"], "v0.4.0")
                    self.assertEqual(failed["status"], "unknown")
                    self.assertIsNone(failed["latest_version"])
                    self.assertIsNone(failed["release_url"])
                    self.assertIn(reason, failed["error"])
            self.assertEqual(check_update("cli", "v0.4.0")["status"], "up_to_date")

    def test_only_one_component_has_a_formal_release(self):
        with patch("worklog_release.github_releases", return_value=[release("v0.3.0")]):
            self.assertEqual(check_update("cli", "v0.4.0")["status"], "ahead")
            missing = check_update("codex", "gui-codex-v0.1.0")
        self.assertEqual(missing["installed_version"], "gui-codex-v0.1.0")
        self.assertIsNone(missing["latest_version"])
        self.assertEqual(missing["status"], "unknown")
        self.assertIn("暂无", missing["error"])

    def test_malformed_versions_cannot_be_compared(self):
        for version in [None, "0.4.0", "v0.04.0", "v0.4.0-preview", "v0.4.0\n", "gui-codex-v0.4.0"]:
            with self.subTest(version=version):
                self.assertIsNone(parse_version(version))
