"""Build and verify the Maya drag-and-drop release without importing Maya."""

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PREFIX = "CylinderResample/"
PACKAGE_PREFIX = "scripts/cylinder_resample/"
VERSION_PATTERN = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\Z")
REQUIRED = {
    "__init__.py", "core.py", "adapter.py", "ui.py", "updater.py",
    "mesh_command.py", "update_ui.py", "config.json",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_version(root=ROOT):
    source = root / "src" / "cylinder_resample" / "__init__.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"), filename=str(source))
    versions = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            versions.append(ast.literal_eval(node.value))
    if len(versions) != 1 or not isinstance(versions[0], str):
        raise ValueError("__version__ must be one literal string assignment")
    version = versions[0]
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError("Release version must be a stable MAJOR.MINOR.PATCH")
    return version


def source_bytes(root, path):
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symlink source is not allowed: " + str(relative))
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Source must be a file inside the repository: " + str(path))
    data = path.read_bytes().replace(b"\r\n", b"\n")
    if path.suffix == ".py":
        compile(data, str(relative), "exec")
    return data


def release_files(root=ROOT):
    files = {}
    package = root / "src" / "cylinder_resample"
    for path in sorted(package.rglob("*")):
        relative = path.relative_to(package)
        if "__pycache__" in relative.parts:
            continue
        if path.is_file() and (path.suffix == ".py" or relative.as_posix() == "config.json"):
            files[PACKAGE_PREFIX + relative.as_posix()] = source_bytes(root, path)
    missing = REQUIRED - {name[len(PACKAGE_PREFIX):] for name in files}
    if missing:
        raise ValueError("Missing package files: " + ", ".join(sorted(missing)))
    json.loads(files[PACKAGE_PREFIX + "config.json"].decode("utf-8-sig"))
    for name in ("install.py", "check_in_maya.py", "README.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md"):
        files[name] = source_bytes(root, root / name)
    for path in sorted((root / "docs").rglob("*.md")):
        files[path.relative_to(root).as_posix()] = source_bytes(root, path)
    if "docs/user-guide.md" not in files:
        raise ValueError("Missing docs/user-guide.md")
    for path in sorted((root / "examples").rglob("*.obj")):
        files[path.relative_to(root).as_posix()] = source_bytes(root, path)
    folded = set()
    for name in files:
        posix = PurePosixPath(name)
        if posix.is_absolute() or ".." in posix.parts or "\\" in name or ":" in name:
            raise ValueError("Unsafe archive member: " + name)
        if name.casefold() in folded:
            raise ValueError("Case-insensitive archive collision: " + name)
        folded.add(name.casefold())
    return files


def build(output_dir, root=ROOT):
    version = read_version(root)
    files = release_files(root)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / ("CylinderResample_Maya2024_v" + version + ".zip")
    temporary = archive.with_name(archive.name + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as package:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo(PREFIX + name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                package.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        os.replace(temporary, archive)
    finally:
        if temporary.exists():
            temporary.unlink()
    archive_data = archive.read_bytes()
    manifest = {
        "schema_version": 1,
        "version": version,
        "maya_min": 2024,
        "maya_max": 2024,
        "archive": {"name": archive.name, "size": len(archive_data), "sha256": digest(archive_data)},
        "files": {name: digest(data) for name, data in sorted(files.items()) if name.startswith(PACKAGE_PREFIX)},
    }
    manifest_path = output / "update-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    checksum_path = output / (archive.name + ".sha256")
    checksum_path.write_text(manifest["archive"]["sha256"] + "  " + archive.name + "\n", encoding="ascii", newline="\n")
    verify(output, root)
    return {"version": version, "archive": str(archive), "manifest": str(manifest_path), "checksum": str(checksum_path)}


def verify(output_dir, root=ROOT):
    output = Path(output_dir).resolve()
    manifest = json.loads((output / "update-manifest.json").read_text(encoding="utf-8"))
    version = read_version(root)
    expected_name = "CylinderResample_Maya2024_v" + version + ".zip"
    if manifest.get("schema_version") != 1 or manifest.get("version") != version:
        raise ValueError("Manifest schema/version mismatch")
    if (manifest.get("maya_min"), manifest.get("maya_max")) != (2024, 2024):
        raise ValueError("Manifest Maya compatibility mismatch")
    archive_info = manifest["archive"]
    if archive_info["name"] != expected_name:
        raise ValueError("Manifest archive name mismatch")
    archive = output / expected_name
    archive_data = archive.read_bytes()
    if len(archive_data) != archive_info["size"] or digest(archive_data) != archive_info["sha256"]:
        raise ValueError("Archive size/SHA-256 mismatch")
    expected = release_files(root)
    expected_package = {name: digest(data) for name, data in expected.items() if name.startswith(PACKAGE_PREFIX)}
    if manifest["files"] != expected_package:
        raise ValueError("Manifest package file list/hash mismatch")
    with zipfile.ZipFile(archive) as package:
        members = package.namelist()
        if len(members) != len(set(members)) or set(members) != {PREFIX + name for name in expected}:
            raise ValueError("Archive file list mismatch")
        if package.testzip() is not None:
            raise ValueError("Archive CRC failed")
        for name, data in expected.items():
            if package.read(PREFIX + name) != data:
                raise ValueError("Archive source mismatch: " + name)
    checksum = (output / (expected_name + ".sha256")).read_text(encoding="ascii")
    if checksum != archive_info["sha256"] + "  " + expected_name + "\n":
        raise ValueError("Checksum file mismatch")
    return {"version": version, "verified": True, "files": len(expected)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--verify-only", action="store_true", help="Verify existing artifacts against current sources")
    parser.add_argument("--check-tag", help="Require tag v<__version__> before building/verifying")
    args = parser.parse_args(argv)
    try:
        version = read_version()
        if args.check_tag and args.check_tag != "v" + version:
            raise ValueError("Tag " + args.check_tag + " does not match __version__ " + version)
        result = verify(args.output_dir) if args.verify_only else build(args.output_dir)
    except (OSError, ValueError, KeyError, SyntaxError, zipfile.BadZipFile) as exc:
        print("Release build failed: " + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
