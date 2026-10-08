"""Offline update security and atomic-install regression checks."""

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import warnings
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cylinder_resample import updater


REPOSITORY = "huang200201-dotcom/maya-cylinder-resample"
PREFIX = "CylinderResample/scripts/cylinder_resample/"


def json_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fixture_package(version="0.3.0", replacements=None, extras=(), omit=()):
    files = {name: b"VALUE = 1\n" for name in updater.REQUIRED_FILES}
    files["__init__.py"] = ('__version__ = "%s"\n' % version).encode("utf-8")
    files["config.json"] = json_bytes({"repository": REPOSITORY})
    files.update(replacements or {})
    stream = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                if name not in omit:
                    archive.writestr(PREFIX + name, data)
            for name, data in extras:
                archive.writestr(name, data)
    data = stream.getvalue()
    name = "CylinderResample_Maya2024_v%s.zip" % version
    asset = {
        "name": name, "size": len(data), "state": "uploaded",
        "url": "https://api.github.com/repos/%s/releases/assets/123" % REPOSITORY,
    }
    manifest = {
        "schema_version": 1, "version": version, "maya_min": 2024, "maya_max": 2024,
        "archive": {"name": name, "size": len(data), "sha256": digest(data)},
        "files": {"scripts/cylinder_resample/" + name: digest(payload)
                  for name, payload in files.items()},
    }
    release = {"repository": REPOSITORY, "version": version,
               "manifest": manifest, "archive_asset": asset}
    return data, release, files


def installed_fixture(parent, version="0.2.0"):
    target = Path(parent) / "cylinder_resample"
    target.mkdir()
    (target / "__init__.py").write_text('__version__ = "%s"\n' % version, encoding="utf-8")
    (target / "old.txt").write_bytes(b"original user-owned package fixture")
    return target


def tree_bytes(path):
    return {item.relative_to(path).as_posix(): item.read_bytes()
            for item in Path(path).rglob("*") if item.is_file()}


class ManifestAndArchiveTests(unittest.TestCase):
    def test_valid_package_only_installs_verified_package_files(self):
        data, release, files = fixture_package(extras=(
            ("CylinderResample/README.md", b"fixture documentation"),
            ("CylinderResample/examples/demo.obj", b"v 0 0 0\n"),
            ("CylinderResample/install.py", b"raise AssertionError('not executed')\n"),
        ))
        manifest = release["manifest"]
        self.assertIs(updater._validate_manifest(manifest, "0.3.0", release["archive_asset"]), manifest)
        self.assertEqual(updater._validated_package(data, manifest), files)

    def test_manifest_rejects_bad_schema_version_compatibility_and_archive_integrity(self):
        _, release, _ = fixture_package()
        mutations = (
            ("schema_version", True), ("schema_version", 2), ("version", "0.3.1"),
            ("maya_min", True), ("maya_min", 2025), ("maya_max", 2023),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                manifest = copy.deepcopy(release["manifest"])
                manifest[field] = value
                with self.assertRaises(updater.UpdaterError):
                    updater._validate_manifest(manifest, "0.3.0", release["archive_asset"])
        for field, value in (("size", 0), ("size", True), ("name", "other.zip"),
                             ("sha256", "f" * 63), ("sha256", "X" * 64)):
            with self.subTest(archive_field=field, value=value):
                manifest = copy.deepcopy(release["manifest"])
                manifest["archive"][field] = value
                with self.assertRaises(updater.UpdaterError):
                    updater._validate_manifest(manifest, "0.3.0", release["archive_asset"])

    def test_manifest_rejects_missing_required_file_and_case_duplicate(self):
        _, release, _ = fixture_package()
        for mutation in ("missing", "duplicate"):
            with self.subTest(mutation=mutation):
                manifest = copy.deepcopy(release["manifest"])
                if mutation == "missing":
                    del manifest["files"]["scripts/cylinder_resample/core.py"]
                else:
                    manifest["files"]["scripts/cylinder_resample/CORE.py"] = "0" * 64
                with self.assertRaises(updater.UpdaterError):
                    updater._validate_manifest(manifest, "0.3.0", release["archive_asset"])

    def test_manifest_rejects_unsafe_nonpackage_and_executable_paths(self):
        _, release, _ = fixture_package()
        paths = ("../escape.py", "/absolute.py", "C:/escape.py", "scripts/cylinder_resample/../escape.py",
                 "scripts/cylinder_resample/a\\b.py", "scripts/cylinder_resample/CON.py",
                 "scripts/cylinder_resample/core.py.", "scripts/cylinder_resample/bad.py ",
                 "scripts/cylinder_resample/core.py:stream", "scripts/cylinder_resample/tool.exe",
                 "scripts/other/tool.py", "scripts/cylinder_resample/.//tool.py")
        for path in paths:
            with self.subTest(path=path):
                manifest = copy.deepcopy(release["manifest"])
                manifest["files"][path] = "0" * 64
                with self.assertRaises(updater.UpdaterError):
                    updater._validate_manifest(manifest, "0.3.0", release["archive_asset"])

    def test_archive_hash_size_and_file_hash_are_all_checked(self):
        data, release, _ = fixture_package()
        for variant in ("archive_hash", "archive_size", "file_hash", "damaged_zip"):
            with self.subTest(variant=variant):
                manifest = copy.deepcopy(release["manifest"])
                payload = data
                if variant == "archive_hash":
                    manifest["archive"]["sha256"] = "0" * 64
                elif variant == "archive_size":
                    manifest["archive"]["size"] += 1
                elif variant == "file_hash":
                    manifest["files"]["scripts/cylinder_resample/core.py"] = "0" * 64
                else:
                    payload = b"not a ZIP"
                    manifest["archive"].update(size=len(payload), sha256=digest(payload))
                with self.assertRaises(updater.UpdaterError):
                    updater._validated_package(payload, manifest)

    def test_archive_rejects_path_traversal_absolute_unknown_and_case_duplicate_entries(self):
        paths = ("CylinderResample/../escape.py", "../escape.py", "/escape.py", "C:/escape.py",
                 "CylinderResample/scripts/cylinder_resample/../escape.py",
                 "CylinderResample/scripts/cylinder_resample/CORE.py", PREFIX + "core.py",
                 "CylinderResample/payload.exe", PREFIX + "unknown.py", "WrongRoot/README.md")
        for path in paths:
            with self.subTest(path=path):
                data, release, _ = fixture_package(extras=((path, b"VALUE=1\n"),))
                with self.assertRaises(updater.UpdaterError):
                    updater._validated_package(data, release["manifest"])

    def test_archive_rejects_symbolic_link_and_missing_manifest_file(self):
        link = zipfile.ZipInfo("CylinderResample/docs/link.md")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        data, release, _ = fixture_package(extras=((link, b"../../outside"),))
        with self.assertRaises(updater.UpdaterError):
            updater._validated_package(data, release["manifest"])
        data, release, _ = fixture_package(omit=("core.py",))
        with self.assertRaises(updater.UpdaterError):
            updater._validated_package(data, release["manifest"])

    def test_archive_limits_reject_excess_files_and_unpacked_bytes(self):
        data, release, _ = fixture_package()
        with patch.object(updater, "FILE_LIMIT", 2):
            with self.assertRaises(updater.UpdaterError):
                updater._validated_package(data, release["manifest"])
        with patch.object(updater, "UNPACKED_LIMIT", 10):
            with self.assertRaises(updater.UpdaterError):
                updater._validated_package(data, release["manifest"])
        with patch.object(updater, "ARCHIVE_LIMIT", 10):
            with self.assertRaises(updater.UpdaterError):
                updater._validated_package(data, release["manifest"])

    def test_python_syntax_and_explicit_matching_version_are_required(self):
        replacements = (
            {"core.py": b"def broken(:\n"},
            {"__init__.py": b"__version__ = '0.4.0'\n"},
            {"__init__.py": b"__version__ = str('0.3.0')\n"},
            {"__init__.py": b"__version__ = '0.3.0'\n__version__ = '0.3.0'\n"},
            {"config.json": b'{"repository":"a/b", "repository":"c/d"}'},
        )
        for replacement in replacements:
            with self.subTest(replacement=replacement):
                data, release, _ = fixture_package(replacements=replacement)
                with self.assertRaises(updater.UpdaterError):
                    updater._validated_package(data, release["manifest"])

    def test_json_duplicate_fields_nonfinite_numbers_and_size_are_rejected(self):
        for payload in (b'{"version": "1", "version": "2"}', b'{"number":NaN}',
                        b'{"number":Infinity}', b'[]', b'\xff', b'{bad}'):
            with self.subTest(payload=payload):
                with self.assertRaises(updater.UpdaterError):
                    updater._json(payload)
        with self.assertRaises(updater.UpdaterError):
            updater._json(b'{"ok":1}', limit=3)


class ReleaseAndNetworkTests(unittest.TestCase):
    def setUp(self):
        self.data, self.checked, _ = fixture_package()
        manifest_data = json_bytes(self.checked["manifest"])
        self.manifest_asset = {
            "name": "update-manifest.json", "size": len(manifest_data), "state": "uploaded",
            "url": "https://api.github.com/repos/%s/releases/assets/124" % REPOSITORY,
        }
        self.release = {
            "draft": False, "prerelease": False, "tag_name": "v0.3.0",
            "html_url": "https://github.com/%s/releases/tag/v0.3.0" % REPOSITORY,
            "body": "Release fixture notes", "assets": [self.checked["archive_asset"], self.manifest_asset],
        }
        self.responses = {
            "https://api.github.com/repos/%s/releases/latest" % REPOSITORY: json_bytes(self.release),
            self.manifest_asset["url"]: manifest_data,
        }

    def test_release_check_downloads_only_metadata_until_install_is_requested(self):
        calls = []

        def request(url, *args, **kwargs):
            calls.append(url)
            return self.responses[url]

        with patch.object(updater, "_request", request):
            result = updater.check_for_update("0.2.0", REPOSITORY)
        self.assertTrue(result["available"])
        self.assertEqual(result["version"], "0.3.0")
        self.assertEqual(result["notes"], "Release fixture notes")
        self.assertEqual(len(calls), 2)
        self.assertNotIn(self.checked["archive_asset"]["url"], calls)

    def test_release_equal_or_older_version_is_not_an_update(self):
        for current in ("0.3.0", "0.4.0", "10.0.0"):
            with self.subTest(current=current), patch.object(updater, "_request", lambda url, *args: self.responses[url]):
                self.assertFalse(updater.check_for_update(current, REPOSITORY)["available"])

    def test_release_rejects_drafts_prereleases_tag_or_asset_owner_mismatch(self):
        for mutation in ("draft", "prerelease", "tag", "release_owner", "asset_owner", "duplicate", "size", "asset_url"):
            with self.subTest(mutation=mutation):
                payload = copy.deepcopy(self.release)
                if mutation in ("draft", "prerelease"):
                    payload[mutation] = True
                elif mutation == "tag":
                    payload["tag_name"] = "v0.3.0-rc1"
                elif mutation == "release_owner":
                    payload["html_url"] = "https://github.com/other/repo/releases/tag/v0.3.0"
                elif mutation == "asset_owner":
                    payload["assets"][0]["url"] = "https://api.github.com/repos/other/repo/releases/assets/123"
                elif mutation == "duplicate":
                    payload["assets"].append(payload["assets"][0])
                elif mutation == "size":
                    payload["assets"][0]["size"] = True
                else:
                    payload["assets"][0]["url"] = 123
                with patch.object(updater, "_request", lambda *args: json_bytes(payload)):
                    with self.assertRaises(updater.UpdaterError):
                        updater.check_for_update("0.2.0", REPOSITORY)

    def test_network_urls_require_official_https_hosts_without_credentials(self):
        invalid = (None, 123, "http://api.github.com/x", "https://api.github.com.evil.test/x",
                   "https://evil.test/x", "https://user:pass@api.github.com/x", "https://api.github.com:444/x",
                   "https://api.github.com/x#fragment", "https://api.github.com/x\r\nHeader: value",
                   "https://api.github.com\\evil.test/x")
        for url in invalid:
            with self.subTest(url=url):
                with self.assertRaises(updater.UpdaterError):
                    updater._validate_download_url(url)

    def test_redirect_strips_authorization_before_github_asset_host(self):
        request = urllib.request.Request("https://api.github.com/repos/a/b/releases/assets/1",
                                         headers={"Authorization": "Bearer fixture-secret"})
        request.add_unredirected_header("Authorization", "Bearer another-secret")
        redirected = updater._SafeRedirect().redirect_request(
            request, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/asset")
        self.assertNotIn("authorization", {key.lower() for key in redirected.headers})
        self.assertNotIn("authorization", {key.lower() for key in redirected.unredirected_hdrs})
        with self.assertRaises(updater.UpdaterError):
            updater._SafeRedirect().redirect_request(request, None, 302, "Found", {}, "https://evil.test/asset")

    def test_request_enforces_content_length_stream_limits_and_sanitizes_http_errors(self):
        class Response:
            def __init__(self, data, length=None):
                self.stream = io.BytesIO(data)
                self.headers = {} if length is None else {"Content-Length": str(length)}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def geturl(self):
                return "https://api.github.com/fixture"

            def read(self, size):
                return self.stream.read(size)

        for response in (Response(b"123456", None), Response(b"123", 10),
                         Response(b"123", 4), Response(b"123", "invalid")):
            with self.subTest(response=response):
                opener = unittest.mock.Mock()
                opener.open.return_value = response
                with patch.object(updater.urllib.request, "build_opener", return_value=opener):
                    with self.assertRaises(updater.UpdaterError):
                        updater._request("https://api.github.com/fixture", 5)
        opener = unittest.mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError(
            "https://api.github.com/private?secret=credential", 403, "fixture-secret-token", {}, None)
        with patch.object(updater.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(updater.UpdaterError) as failure:
                updater._request("https://api.github.com/fixture", 5, token="fixture-secret-token")
        self.assertNotIn("fixture-secret-token", str(failure.exception))
        self.assertNotIn("credential", str(failure.exception))

    def test_request_sends_token_only_to_github_api(self):
        requests = []

        class Response:
            headers = {"Content-Length": "0"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def geturl(self):
                return "https://github.com/fixture"

            def read(self, size):
                return b""

        opener = unittest.mock.Mock()
        opener.open.side_effect = lambda request, **kwargs: requests.append(request) or Response()
        with patch.object(updater.urllib.request, "build_opener", return_value=opener):
            updater._request("https://api.github.com/fixture", 10, token="fixture-secret")
            updater._request("https://github.com/fixture", 10, token="fixture-secret")
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer fixture-secret")
        self.assertIsNone(requests[1].get_header("Authorization"))


class AtomicInstallTests(unittest.TestCase):
    def test_target_accepts_safe_windows_short_name_alias_without_realpath_equality(self):
        with tempfile.TemporaryDirectory() as directory:
            target = installed_fixture(directory)
            with patch.object(updater.os.path, "realpath", return_value=str(target.parent / "long-name" / target.name)):
                self.assertEqual(updater._target(target), target.absolute())

    def test_target_rejects_symlinks_and_windows_reparse_points_in_each_ancestor(self):
        real_lstat = os.lstat
        for selected in ("target", "parent"):
            for mode in ("symlink", "junction"):
                with self.subTest(selected=selected, mode=mode), tempfile.TemporaryDirectory() as directory:
                    target = installed_fixture(directory)
                    unsafe = target if selected == "target" else target.parent

                    def lstat(path, *args, **kwargs):
                        result = real_lstat(path, *args, **kwargs)
                        if os.path.normcase(os.path.abspath(str(path))) == os.path.normcase(str(unsafe)):
                            return types.SimpleNamespace(
                                st_mode=stat.S_IFLNK if mode == "symlink" else result.st_mode,
                                st_file_attributes=0x400 if mode == "junction" else 0)
                        return result

                    with patch.object(updater.os, "lstat", lstat):
                        with self.assertRaises(updater.UpdaterError):
                            updater._target(target)

    def test_rollback_accepts_safe_realpath_alias_and_rejects_redirected_backup(self):
        data, release, _ = fixture_package()
        with tempfile.TemporaryDirectory() as directory:
            target = installed_fixture(directory)
            original = tree_bytes(target)
            with patch.object(updater, "_request", return_value=data):
                result = updater.install_release(release, target)
            with patch.object(updater.os.path, "realpath", return_value=str(target.parent / "expanded-alias")):
                updater.rollback_install(result)
            self.assertEqual(tree_bytes(target), original)
        real_lstat = os.lstat
        for mode in ("symlink", "junction"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                target = installed_fixture(directory)
                with patch.object(updater, "_request", return_value=data):
                    result = updater.install_release(release, target)
                installed = tree_bytes(target)
                backup = Path(result["backup_dir"])

                def lstat(path, *args, **kwargs):
                    actual = real_lstat(path, *args, **kwargs)
                    if os.path.normcase(os.path.abspath(str(path))) == os.path.normcase(str(backup)):
                        return types.SimpleNamespace(
                            st_mode=stat.S_IFLNK if mode == "symlink" else actual.st_mode,
                            st_file_attributes=0x400 if mode == "junction" else 0)
                    return actual

                with patch.object(updater.os, "lstat", lstat):
                    with self.assertRaises(updater.UpdaterError):
                        updater.rollback_install(result)
                self.assertEqual(tree_bytes(target), installed)

    def test_verified_install_retains_backup_and_supports_safe_rollback(self):
        data, release, files = fixture_package()
        with tempfile.TemporaryDirectory() as directory:
            target = installed_fixture(directory)
            original = tree_bytes(target)
            with patch.object(updater, "_request", return_value=data):
                result = updater.install_release(release, target)
            self.assertEqual(tree_bytes(target), files)
            self.assertEqual(tree_bytes(result["backup_dir"]), original)
            self.assertFalse((Path(directory) / ".cylinder_resample-update.lock").exists())
            restored = updater.rollback_install(result)
            self.assertEqual(tree_bytes(target), original)
            self.assertEqual(tree_bytes(restored["failed_dir"]), files)
            self.assertEqual(restored["version"], "0.2.0")

    def test_failed_staging_swap_restores_original_and_removes_stage_and_lock(self):
        data, release, _ = fixture_package()
        real_replace = os.replace

        def replace(source, destination):
            if Path(source).name.startswith(".cylinder_resample.stage-"):
                raise OSError("fixture interrupted swap")
            return real_replace(source, destination)

        with tempfile.TemporaryDirectory() as directory:
            target = installed_fixture(directory)
            original = tree_bytes(target)
            with patch.object(updater, "_request", return_value=data), patch.object(updater.os, "replace", replace):
                with self.assertRaises(updater.UpdaterError):
                    updater.install_release(release, target)
            self.assertEqual(tree_bytes(target), original)
            self.assertEqual([path.name for path in Path(directory).iterdir()], ["cylinder_resample"])

    def test_validation_failure_does_not_touch_original_install(self):
        data, release, _ = fixture_package(replacements={"core.py": b"invalid python @\n"})
        with tempfile.TemporaryDirectory() as directory:
            target = installed_fixture(directory)
            original = tree_bytes(target)
            with patch.object(updater, "_request", return_value=data):
                with self.assertRaises(updater.UpdaterError):
                    updater.install_release(release, target)
            self.assertEqual(tree_bytes(target), original)
            self.assertEqual([path.name for path in Path(directory).iterdir()], ["cylinder_resample"])

    def test_equal_and_older_versions_never_download_or_change_installed_files(self):
        _, release, _ = fixture_package()
        for current in ("0.3.0", "0.4.0"):
            with self.subTest(current=current), tempfile.TemporaryDirectory() as directory:
                target = installed_fixture(directory, current)
                original = tree_bytes(target)
                with patch.object(updater, "_request") as request:
                    with self.assertRaises(updater.UpdaterError):
                        updater.install_release(release, target)
                request.assert_not_called()
                self.assertEqual(tree_bytes(target), original)

    def test_lock_prevents_competing_install_without_changing_original_or_existing_lock(self):
        data, release, _ = fixture_package()
        with tempfile.TemporaryDirectory() as directory:
            target = installed_fixture(directory)
            original = tree_bytes(target)
            lock = Path(directory) / ".cylinder_resample-update.lock"
            lock.write_bytes(b"another updater fixture")
            with patch.object(updater, "_request", return_value=data):
                with self.assertRaises(updater.UpdaterError):
                    updater.install_release(release, target)
            self.assertEqual(tree_bytes(target), original)
            self.assertEqual(lock.read_bytes(), b"another updater fixture")

    def test_repository_mismatch_cannot_install_valid_hashed_package(self):
        data, release, _ = fixture_package(replacements={"config.json": json_bytes({"repository": "other/repo"})})
        with tempfile.TemporaryDirectory() as directory:
            target = installed_fixture(directory)
            original = tree_bytes(target)
            with patch.object(updater, "_request", return_value=data):
                with self.assertRaises(updater.UpdaterError):
                    updater.install_release(release, target)
            self.assertEqual(tree_bytes(target), original)

    def test_restore_rejects_unrelated_backup_and_stale_install_record(self):
        data, release, _ = fixture_package()
        with tempfile.TemporaryDirectory() as directory:
            target = installed_fixture(directory)
            with patch.object(updater, "_request", return_value=data):
                result = updater.install_release(release, target)
            installed = tree_bytes(target)
            wrong = dict(result, backup_dir=str(Path(directory) / "unrelated"))
            with self.assertRaises(updater.UpdaterError):
                updater.rollback_install(wrong)
            self.assertEqual(tree_bytes(target), installed)
            (target / "__init__.py").write_text('__version__ = "0.4.0"\n', encoding="utf-8")
            with self.assertRaises(updater.UpdaterError):
                updater.rollback_install(result)
            self.assertEqual(updater._installed_version(target), "0.4.0")


class MayaCompatibilityTests(unittest.TestCase):
    def broad_release(self):
        data, release, files = fixture_package()
        release["manifest"].update(maya_min=2022, maya_max=2027)
        return data, release, files

    def test_manifest_accepts_each_supported_maya_year_and_point_release(self):
        _, release, _ = self.broad_release()
        for year in range(2022, 2028):
            for value in (year, str(year), str(year) + ".2"):
                with self.subTest(maya_version=value):
                    manifest = release["manifest"]
                    self.assertIs(updater._validate_manifest(
                        manifest, "0.3.0", release["archive_asset"], maya_version=value), manifest)

    def test_manifest_retains_legacy_default_and_allows_future_compatible_ranges(self):
        _, release, _ = fixture_package()
        manifest = release["manifest"]
        self.assertIs(updater._validate_manifest(manifest, "0.3.0", release["archive_asset"]), manifest)
        manifest.update(maya_min=2022, maya_max=2028)
        self.assertIs(updater._validate_manifest(
            manifest, "0.3.0", release["archive_asset"], maya_version=2027), manifest)

    def test_manifest_rejects_invalid_ranges_and_unsupported_year(self):
        _, release, _ = self.broad_release()
        for minimum, maximum in ((True, 2027), (2022, True), (2022.0, 2027),
                                 ("2022", 2027), (2022, None), (2027, 2022),
                                 (1999, 2027), (2022, 10000)):
            with self.subTest(minimum=minimum, maximum=maximum):
                manifest = dict(release["manifest"], maya_min=minimum, maya_max=maximum)
                with self.assertRaises(updater.UpdaterError):
                    updater._validate_manifest(manifest, "0.3.0", release["archive_asset"], maya_version=2024)
        for year in (2021, 2028, True, 2024.0, "Maya 2024", "2024foo", "2024.2x", "", []):
            with self.subTest(maya_version=year):
                with self.assertRaises(updater.UpdaterError):
                    updater._validate_manifest(
                        release["manifest"], "0.3.0", release["archive_asset"], maya_version=year)

    def test_manifest_rejection_names_actual_maya_version(self):
        _, release, _ = fixture_package()
        for year in (2022, 2023, 2025, 2026, 2027):
            with self.subTest(maya_version=year):
                with self.assertRaisesRegex(updater.UpdaterError, "Maya " + str(year)):
                    updater._validate_manifest(
                        release["manifest"], "0.3.0", release["archive_asset"], maya_version=year)

    def test_check_for_update_records_actual_maya_year(self):
        _, checked, _ = self.broad_release()
        manifest_bytes = json_bytes(checked["manifest"])
        manifest_asset = {
            "name": "update-manifest.json", "size": len(manifest_bytes), "state": "uploaded",
            "url": "https://api.github.com/repos/%s/releases/assets/124" % REPOSITORY,
        }
        metadata = {
            "draft": False, "prerelease": False, "tag_name": "v0.3.0",
            "html_url": "https://github.com/%s/releases/tag/v0.3.0" % REPOSITORY,
            "body": "Multi-Maya fixture", "assets": [checked["archive_asset"], manifest_asset],
        }
        responses = {
            "https://api.github.com/repos/%s/releases/latest" % REPOSITORY: json_bytes(metadata),
            manifest_asset["url"]: manifest_bytes,
        }
        for year in range(2022, 2028):
            with self.subTest(maya_version=year), patch.object(
                    updater, "_request", side_effect=lambda url, *args: responses[url]):
                result = updater.check_for_update("0.2.0", REPOSITORY, maya_version=str(year) + ".1")
                self.assertEqual(result["maya_version"], year)
                self.assertTrue(result["available"])
                self.assertEqual(result["archive_asset"]["name"], "CylinderResample_Maya2024_v0.3.0.zip")

    def test_invalid_actual_version_is_rejected_before_network_access(self):
        for year in (2021, 2028, True, "invalid"):
            with self.subTest(maya_version=year), patch.object(updater, "_request") as request:
                with self.assertRaises(updater.UpdaterError):
                    updater.check_for_update("0.2.0", maya_version=year)
                request.assert_not_called()

    def test_install_uses_checked_maya_year_without_explicit_argument(self):
        data, release, files = self.broad_release()
        for year in range(2022, 2028):
            with self.subTest(maya_version=year), tempfile.TemporaryDirectory() as directory:
                target = installed_fixture(directory)
                checked = dict(release, maya_version=year)
                with patch.object(updater, "_request", return_value=data):
                    result = updater.install_release(checked, target)
                self.assertEqual(result["maya_version"], year)
                self.assertEqual(tree_bytes(target), files)

    def test_install_accepts_explicit_actual_version_on_legacy_record(self):
        data, release, files = self.broad_release()
        with tempfile.TemporaryDirectory() as directory:
            target = installed_fixture(directory)
            with patch.object(updater, "_request", return_value=data):
                result = updater.install_release(release, target, maya_version="2027.1")
            self.assertEqual(result["maya_version"], 2027)
            self.assertEqual(tree_bytes(target), files)

    def test_install_conflicting_or_invalid_checked_year_never_touches_files(self):
        _, release, _ = self.broad_release()
        for checked_year, actual_year in ((2022, 2024), (2027, 2026), (2028, None),
                                          (True, 2024), ("invalid", 2024)):
            with self.subTest(checked=checked_year, actual=actual_year), tempfile.TemporaryDirectory() as directory:
                target = installed_fixture(directory)
                original = tree_bytes(target)
                with patch.object(updater, "_request") as request:
                    with self.assertRaises(updater.UpdaterError):
                        updater.install_release(dict(release, maya_version=checked_year),
                                                target, maya_version=actual_year)
                request.assert_not_called()
                self.assertEqual(tree_bytes(target), original)

    def test_install_revalidates_manifest_against_checked_year(self):
        _, release, _ = fixture_package()
        with tempfile.TemporaryDirectory() as directory:
            target = installed_fixture(directory)
            original = tree_bytes(target)
            with patch.object(updater, "_request") as request:
                with self.assertRaisesRegex(updater.UpdaterError, "Maya 2022"):
                    updater.install_release(dict(release, maya_version=2022), target)
            request.assert_not_called()
            self.assertEqual(tree_bytes(target), original)

    def test_python_version_accepts_string_literals_across_ast_versions(self):
        for payload in (b"__version__ = '0.3.0'\n", b'__version__ = u"0.3.0"\n',
                        b"__version__ = ('0.3.0')\n"):
            with self.subTest(payload=payload):
                self.assertEqual(updater._python_version(payload), "0.3.0")

    def test_python_version_rejects_dynamic_duplicate_and_nonstring_values(self):
        payloads = (
            b"__version__ = str('0.3.0')\n",
            b"__version__ = '0.3.' + '0'\n",
            b"__version__ = f'0.3.0'\n",
            b"__version__ = b'0.3.0'\n",
            b"__version__ = 0.3\n",
            b"__version__ = ('0.3.0',)\n",
            b"__version__ = '0.3.0'\n__version__ = '0.3.0'\n",
            b"__version__ = '0.3.0'\nif True:\n    __version__ = '0.4.0'\n",
            b"__version__ = '0.3.0'\n__version__ += '.1'\n",
            b"__version__ = '0.3.0'\ndel __version__\n",
            b"__version__: str = '0.3.0'\n",
            b"if True:\n    __version__ = '0.3.0'\n",
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(updater.UpdaterError):
                    updater._python_version(payload)


if __name__ == "__main__":
    unittest.main()
