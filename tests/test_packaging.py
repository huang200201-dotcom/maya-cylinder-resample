"""Release ZIPs and update manifests are reproducible and agree byte-for-byte."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cylinder_resample import __version__, updater


class ReleasePackagingTests(unittest.TestCase):
    def build(self, directory, *options):
        return subprocess.run(
            [sys.executable, "-B", str(ROOT / "tools" / "build_release.py"),
             "--output-dir", str(directory), *options], cwd=str(ROOT),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)

    def test_release_manifest_and_zip_match_and_repeated_build_is_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            first = self.build(output)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            archive_path = output / ("CylinderResample_Maya2024_v%s.zip" % __version__)
            universal_path = output / ("CylinderResample_Maya2022-2027_v%s.zip" % __version__)
            manifest_path = output / "update-manifest.json"
            archive_bytes = archive_path.read_bytes()
            manifest_bytes = manifest_path.read_bytes()
            manifest = json.loads(manifest_bytes)
            self.assertEqual(manifest["version"], __version__)
            self.assertEqual((manifest["maya_min"], manifest["maya_max"]), (2022, 2027))
            self.assertEqual(manifest["archive"]["name"], archive_path.name)
            self.assertEqual(manifest["archive"]["size"], len(archive_bytes))
            self.assertEqual(manifest["archive"]["sha256"], hashlib.sha256(archive_bytes).hexdigest())
            self.assertEqual(universal_path.read_bytes(), archive_bytes)
            for package_path in (archive_path, universal_path):
                self.assertEqual(
                    package_path.with_name(package_path.name + ".sha256").read_bytes(),
                    (manifest["archive"]["sha256"] + "  " + package_path.name + "\n").encode("ascii"))
            updater._validate_manifest(manifest, __version__, manifest["archive"])
            installed_files = updater._validated_package(archive_bytes, manifest)
            expected = {path.relative_to(ROOT / "src" / "cylinder_resample").as_posix(): path.read_bytes().replace(b"\r\n", b"\n")
                        for path in (ROOT / "src" / "cylinder_resample").rglob("*")
                        if path.is_file() and (path.suffix == ".py" or path.name == "config.json")}
            self.assertEqual(installed_files, expected)
            with zipfile.ZipFile(archive_path) as archive:
                self.assertIsNone(archive.testzip())
                self.assertTrue(all(name.startswith("CylinderResample/") for name in archive.namelist()))
                self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in archive.namelist()))
                self.assertIn("CylinderResample/install.py", archive.namelist())
                self.assertIn("CylinderResample/README.md", archive.namelist())
                self.assertIn("CylinderResample/scripts/cylinder_resample/compat.py", archive.namelist())
            second = self.build(output)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(archive_path.read_bytes(), archive_bytes)
            self.assertEqual(universal_path.read_bytes(), archive_bytes)
            self.assertEqual(manifest_path.read_bytes(), manifest_bytes)

    def test_verification_rejects_modified_universal_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            built = self.build(output)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            universal_path = output / ("CylinderResample_Maya2022-2027_v%s.zip" % __version__)
            universal_path.write_bytes(universal_path.read_bytes() + b"modified")
            verified = self.build(output, "--verify-only")
            self.assertNotEqual(verified.returncode, 0)
            self.assertIn("archive contents differ", verified.stderr)

    def test_mismatched_release_tag_is_rejected_without_creating_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.build(directory, "--check-tag", "v9.9.9")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
