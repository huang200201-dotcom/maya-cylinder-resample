"""Verified GitHub Release updates, independent of Maya and its UI thread."""

import ast
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
import zlib


DEFAULT_REPOSITORY = "huang200201-dotcom/maya-cylinder-resample"
JSON_LIMIT = 1024 * 1024
ARCHIVE_LIMIT = 32 * 1024 * 1024
UNPACKED_LIMIT = 64 * 1024 * 1024
FILE_LIMIT = 256
NETWORK_TIMEOUT = 30
PACKAGE_PREFIX = "scripts/cylinder_resample/"
ARCHIVE_PREFIX = "CylinderResample/"
REQUIRED_FILES = {
    "__init__.py", "core.py", "adapter.py", "ui.py", "updater.py",
    "config.json", "mesh_command.py", "update_ui.py", "compat.py", "spacing.py",
}
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_ASSET_HOSTS = {
    "github.com", "api.github.com", "objects.githubusercontent.com",
    "release-assets.githubusercontent.com", "github-releases.githubusercontent.com",
}
_RESERVED = {"CON", "PRN", "AUX", "NUL"}
_RESERVED.update("COM{}".format(i) for i in range(1, 10))
_RESERVED.update("LPT{}".format(i) for i in range(1, 10))


class UpdaterError(RuntimeError):
    """An actionable error that does not disclose credentials or signed URLs."""


def _version(value):
    if not isinstance(value, str) or len(value) > 40 or not _VERSION.fullmatch(value):
        raise UpdaterError("版本号必须为正式版本，例如 0.3.0。")
    return tuple(int(piece) for piece in value.split("."))


def _repository(value=None):
    if value is None:
        config = Path(__file__).with_name("config.json")
        try:
            if config.is_file():
                with config.open("rb") as stream:
                    value = _json(stream.read(JSON_LIMIT + 1), JSON_LIMIT).get("repository")
        except (OSError, AttributeError):
            raise UpdaterError("无法读取插件更新配置。") from None
        value = value or DEFAULT_REPOSITORY
    if not isinstance(value, str) or not _REPOSITORY.fullmatch(value):
        raise UpdaterError("GitHub 仓库地址必须为 用户名/仓库名。")
    owner, name = value.split("/")
    if name in (".", "..") or name.endswith(".git"):
        raise UpdaterError("GitHub 仓库地址格式不正确。")
    return owner + "/" + name


def _token(value=None):
    if value is None:
        value = os.environ.get("CYLINDER_RESAMPLE_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 8192 or "\r" in value or "\n" in value:
        raise UpdaterError("GitHub 访问令牌格式不正确。")
    return value.strip() or None


def _validate_download_url(url):
    if not isinstance(url, str) or not url or len(url) > 16384:
        raise UpdaterError("更新下载地址不安全。")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        raise UpdaterError("更新下载地址不安全。") from None
    if (parsed.scheme != "https" or parsed.hostname not in _ASSET_HOSTS
            or port not in (None, 443) or parsed.username is not None
            or parsed.password is not None or parsed.fragment):
        raise UpdaterError("更新下载地址必须来自 GitHub 官方 HTTPS 服务。")
    if any(character in url for character in ("\r", "\n", "\\", "\x00")):
        raise UpdaterError("更新下载地址不安全。")
    return parsed


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        parsed = _validate_download_url(new_url)
        redirected = super().redirect_request(request, response, code, message, headers, new_url)
        if redirected is not None and parsed.hostname != "api.github.com":
            for key in tuple(redirected.headers):
                if key.lower() == "authorization":
                    del redirected.headers[key]
            for key in tuple(redirected.unredirected_hdrs):
                if key.lower() == "authorization":
                    del redirected.unredirected_hdrs[key]
        return redirected


def _request(url, limit, token=None, accept="application/vnd.github+json"):
    parsed = _validate_download_url(url)
    headers = {
        "Accept": accept, "User-Agent": "Maya-Cylinder-Resample-Updater",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token and parsed.hostname == "api.github.com":
        headers["Authorization"] = "Bearer " + _token(token)
    request = urllib.request.Request(url, headers=headers)
    try:
        opener = urllib.request.build_opener(_SafeRedirect())
        with opener.open(request, timeout=NETWORK_TIMEOUT) as response:
            _validate_download_url(response.geturl())
            length = response.headers.get("Content-Length")
            if length is not None:
                try:
                    length = int(length)
                except (ValueError, TypeError):
                    raise UpdaterError("更新服务器返回了无效文件大小。") from None
                if length < 0 or length > limit:
                    raise UpdaterError("更新文件超过允许的大小。")
            chunks = []
            received = 0
            while True:
                chunk = response.read(min(65536, limit + 1 - received))
                if not chunk:
                    break
                received += len(chunk)
                if received > limit:
                    raise UpdaterError("更新文件超过允许的大小。")
                chunks.append(chunk)
            if length is not None and received != length:
                raise UpdaterError("更新下载不完整，请重试。")
            return b"".join(chunks)
    except urllib.error.HTTPError as error:
        messages = {
            401: "GitHub 访问令牌无效。",
            403: "GitHub 暂时限制访问，或令牌没有读取仓库的权限。",
            404: "未找到公开的正式 Release；私有仓库需要读取权限。",
            429: "GitHub 请求过于频繁，请稍后重试。",
        }
        raise UpdaterError(messages.get(error.code, "GitHub 更新服务暂时不可用。")) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise UpdaterError("连接 GitHub 失败，请检查网络或代理后重试。") from None


def _json(data, limit=JSON_LIMIT):
    if not isinstance(data, bytes) or len(data) > limit:
        raise UpdaterError("更新信息超过允许的大小。")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise UpdaterError("更新信息包含重复字段。")
            result[key] = value
        return result

    def invalid_constant(value):
        raise UpdaterError("更新信息包含无效数字。")

    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=unique_pairs,
                            parse_constant=invalid_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise UpdaterError("更新信息不是有效的 UTF-8 JSON。") from None
    if not isinstance(result, dict):
        raise UpdaterError("更新信息格式不正确。")
    return result


def _safe_relative(path, directory=False):
    if not isinstance(path, str) or not path or len(path) > 240:
        raise UpdaterError("更新包包含无效文件路径。")
    if directory and path.endswith("/"):
        path = path[:-1]
    pieces = path.split("/")
    if any(piece in ("", ".", "..") for piece in pieces):
        raise UpdaterError("更新包包含越界文件路径。")
    for piece in pieces:
        if (piece.endswith((".", " ")) or any(ord(char) < 32 for char in piece)
                or any(char in piece for char in "\\:<>\"|?*")
                or piece.split(".", 1)[0].upper() in _RESERVED):
            raise UpdaterError("更新包包含不安全的文件名。")
    return "/".join(pieces)


def _asset(asset, repository, expected_name, limit):
    if not isinstance(asset, dict) or asset.get("name") != expected_name:
        raise UpdaterError("Release 缺少所需更新附件。")
    size = asset.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= limit:
        raise UpdaterError("Release 附件大小无效。")
    parsed = _validate_download_url(asset.get("url"))
    expected = "/repos/{}/releases/assets/".format(repository)
    if (parsed.hostname != "api.github.com" or parsed.query
            or not parsed.path.startswith(expected)
            or not parsed.path[len(expected):].isdigit()):
        raise UpdaterError("Release 附件地址与仓库不一致。")
    if asset.get("state", "uploaded") != "uploaded":
        raise UpdaterError("Release 附件尚未上传完成。")
    return {"name": expected_name, "size": size, "url": asset["url"]}


def _maya_version(value=None):
    from .compat import MAX_MAYA, MIN_MAYA, maya_year
    try:
        year = maya_year(2024 if value is None else value)
    except (TypeError, ValueError):
        raise UpdaterError("无法识别用于更新检查的 Maya 版本。") from None
    if not MIN_MAYA <= year <= MAX_MAYA:
        raise UpdaterError("插件仅支持 Maya {} 至 {}。".format(MIN_MAYA, MAX_MAYA))
    return year


def _validate_manifest(manifest, version, archive_asset, maya_version=None):
    year = _maya_version(maya_version)
    _version(version)
    if (not isinstance(manifest, dict) or not isinstance(manifest.get("schema_version"), int)
            or manifest.get("schema_version") != 1
            or isinstance(manifest.get("schema_version"), bool)
            or manifest.get("version") != version):
        raise UpdaterError("更新清单版本与 Release 不一致。")
    minimum, maximum = manifest.get("maya_min"), manifest.get("maya_max")
    if (not isinstance(minimum, int) or isinstance(minimum, bool)
            or not isinstance(maximum, int) or isinstance(maximum, bool)
            or not 2000 <= minimum <= maximum <= 9999):
        raise UpdaterError("更新清单的 Maya 兼容范围无效。")
    if not minimum <= year <= maximum:
        raise UpdaterError("该更新不支持 Maya {}；支持范围为 {} 至 {}。".format(year, minimum, maximum))
    archive = manifest.get("archive")
    if (not isinstance(archive, dict) or archive.get("name") != archive_asset["name"]
            or archive.get("size") != archive_asset["size"]
            or not isinstance(archive.get("size"), int)
            or isinstance(archive.get("size"), bool)
            or not isinstance(archive.get("sha256"), str)
            or not _SHA256.fullmatch(archive["sha256"])):
        raise UpdaterError("更新包大小或 SHA-256 清单无效。")
    files = manifest.get("files")
    if not isinstance(files, dict) or not 1 <= len(files) <= FILE_LIMIT:
        raise UpdaterError("更新清单中的文件数量无效。")
    names = set()
    for name, checksum in files.items():
        _safe_relative(name)
        if not name.startswith(PACKAGE_PREFIX):
            raise UpdaterError("更新清单包含插件目录之外的文件。")
        relative = name[len(PACKAGE_PREFIX):]
        if not (relative.endswith(".py") or relative == "config.json"):
            raise UpdaterError("更新清单包含不允许安装的文件类型。")
        if name.casefold() in names:
            raise UpdaterError("更新清单包含重复文件路径。")
        names.add(name.casefold())
        if not isinstance(checksum, str) or not _SHA256.fullmatch(checksum):
            raise UpdaterError("更新清单包含无效的 SHA-256。")
    if not {PACKAGE_PREFIX + name for name in REQUIRED_FILES}.issubset(files):
        raise UpdaterError("更新清单缺少必需的插件文件。")
    return manifest


def _python_version(data):
    try:
        tree = ast.parse(data.decode("utf-8-sig"))
    except (UnicodeError, SyntaxError, ValueError, RecursionError):
        raise UpdaterError("更新包的 Python 文件无法解析。") from None
    values = []
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == "__version__"
                   for target in statement.targets):
                values.append(statement.value)
    bindings = [node for node in ast.walk(tree)
                if isinstance(node, ast.Name) and node.id == "__version__"
                and isinstance(node.ctx, (ast.Store, ast.Del))]
    literal_types = (ast.Str, getattr(ast, "Constant", ast.Str))
    if len(values) != 1 or len(bindings) != 1 or not isinstance(values[0], literal_types):
        raise UpdaterError("插件包缺少明确的版本号。")
    try:
        value = ast.literal_eval(values[0])
    except (ValueError, TypeError, SyntaxError, RecursionError):
        raise UpdaterError("插件包缺少明确的版本号。") from None
    _version(value)
    return value


def _validated_package(archive_bytes, manifest):
    archive_info = manifest["archive"]
    if (len(archive_bytes) != archive_info["size"] or len(archive_bytes) > ARCHIVE_LIMIT
            or hashlib.sha256(archive_bytes).hexdigest() != archive_info["sha256"]):
        raise UpdaterError("更新包 SHA-256 或文件大小校验失败，未修改原插件。")
    contents = {}
    names = set()
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            entries = archive.infolist()
            if len(entries) > FILE_LIMIT:
                raise UpdaterError("更新包包含过多文件。")
            for entry in entries:
                if entry.orig_filename != entry.filename:
                    raise UpdaterError("更新包包含无效文件路径。")
                name = _safe_relative(entry.filename, entry.is_dir())
                if name.casefold() in names:
                    raise UpdaterError("更新包包含重复文件路径。")
                names.add(name.casefold())
                if not (name == ARCHIVE_PREFIX[:-1] or name.startswith(ARCHIVE_PREFIX)):
                    raise UpdaterError("更新包根目录不正确。")
                mode = entry.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or entry.flag_bits & 1:
                    raise UpdaterError("更新包不允许符号链接或加密文件。")
                if entry.is_dir():
                    if entry.file_size:
                        raise UpdaterError("更新包包含无效目录。")
                    continue
                if kind == stat.S_IFDIR:
                    raise UpdaterError("更新包包含无效文件。")
                total += entry.file_size
                if entry.file_size < 0 or total > UNPACKED_LIMIT:
                    raise UpdaterError("更新包解压后超过允许的大小。")
                relative = name[len(ARCHIVE_PREFIX):]
                package_file = relative.startswith(PACKAGE_PREFIX)
                if package_file:
                    short = relative[len(PACKAGE_PREFIX):]
                    if relative not in manifest["files"]:
                        raise UpdaterError("更新包与文件清单不一致。")
                    data = archive.read(entry)
                    if hashlib.sha256(data).hexdigest() != manifest["files"][relative]:
                        raise UpdaterError("插件文件 SHA-256 校验失败，未修改原插件。")
                    if short.endswith(".py"):
                        try:
                            ast.parse(data.decode("utf-8-sig"), filename=short)
                        except (UnicodeError, SyntaxError, ValueError, RecursionError):
                            raise UpdaterError("更新包包含无法解析的 Python 文件。") from None
                    elif short == "config.json":
                        _json(data)
                    contents[short] = data
                elif not (relative in {"install.py", "check_in_maya.py", "README.md", "LICENSE", "CHANGELOG.md",
                                      "SECURITY.md", "CONTRIBUTING.md"}
                          or relative.startswith("docs/") and relative.endswith((".md", ".txt"))
                          or relative.startswith("examples/") and relative.endswith(".obj")):
                    raise UpdaterError("更新包包含不允许的附件文件类型。")
            expected = {name[len(PACKAGE_PREFIX):] for name in manifest["files"]}
            if set(contents) != expected:
                raise UpdaterError("更新包缺少清单中声明的插件文件。")
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError, zlib.error, ValueError):
        raise UpdaterError("更新包损坏或压缩格式不受支持。") from None
    if _python_version(contents["__init__.py"]) != manifest["version"]:
        raise UpdaterError("插件文件版本与更新清单不一致。")
    return contents


def check_for_update(current_version=None, repository=None, token=None, maya_version=None):
    year = _maya_version(maya_version)
    if current_version is None:
        from . import __version__
        current_version = __version__
    current = _version(current_version)
    repository = _repository(repository)
    token = _token(token)
    url = "https://api.github.com/repos/{}/releases/latest".format(repository)
    release = _json(_request(url, JSON_LIMIT, token))
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise UpdaterError("更新服务返回的不是正式 Release。")
    tag = release.get("tag_name")
    version = tag[1:] if isinstance(tag, str) and tag.startswith("v") else tag
    latest = _version(version)
    release_url = release.get("html_url")
    parsed = _validate_download_url(release_url)
    if (parsed.hostname != "github.com" or parsed.query
            or parsed.path != "/{}/releases/tag/{}".format(repository, tag)):
        raise UpdaterError("Release 地址与仓库不一致。")
    assets = release.get("assets")
    if not isinstance(assets, list) or len(assets) > FILE_LIMIT:
        raise UpdaterError("Release 附件信息无效。")
    archive_name = "CylinderResample_Maya2024_v{}.zip".format(version)
    selected = {}
    for asset in assets:
        if isinstance(asset, dict) and asset.get("name") in (archive_name, "update-manifest.json"):
            if asset["name"] in selected:
                raise UpdaterError("Release 包含重名更新附件。")
            selected[asset["name"]] = asset
    archive_asset = _asset(selected.get(archive_name), repository, archive_name, ARCHIVE_LIMIT)
    manifest_asset = _asset(selected.get("update-manifest.json"), repository, "update-manifest.json", JSON_LIMIT)
    manifest_bytes = _request(manifest_asset["url"], JSON_LIMIT, token, "application/octet-stream")
    if len(manifest_bytes) != manifest_asset["size"]:
        raise UpdaterError("更新清单下载不完整。")
    manifest = _validate_manifest(_json(manifest_bytes), version, archive_asset, maya_version=year)
    notes = release.get("body") or ""
    if not isinstance(notes, str):
        raise UpdaterError("Release 更新说明无效。")
    return {
        "available": latest > current, "current": current_version, "version": version,
        "release_url": release_url, "notes": notes, "repository": repository,
        "assets": [archive_asset, manifest_asset], "archive_asset": archive_asset,
        "manifest_asset": manifest_asset, "manifest": manifest, "maya_version": year,
    }


def _reject_reparse_ancestors(path):
    # Windows 8.3 names are aliases, not links; inspect metadata instead.
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    try:
        for candidate in (path, *path.parents):
            details = os.lstat(str(candidate))
            attributes = getattr(details, "st_file_attributes", 0) or 0
            if stat.S_ISLNK(details.st_mode) or attributes & reparse_flag:
                raise UpdaterError("插件目录及其父目录不能包含符号链接或 Windows 重解析点。")
    except OSError:
        raise UpdaterError("无法检查插件目录及其父目录的安全状态。") from None


def _target(install_dir=None):
    if install_dir is not None and not isinstance(install_dir, (str, os.PathLike)):
        raise UpdaterError("更新目标目录无效。")
    supplied = Path(install_dir) if install_dir is not None else Path(__file__).parent
    absolute = Path(os.path.abspath(str(supplied)))
    if absolute.name != "cylinder_resample" or not absolute.is_dir():
        raise UpdaterError("更新目标必须为已安装的 cylinder_resample 实际目录。")
    _reject_reparse_ancestors(absolute)
    return absolute


def _installed_version(target):
    init = target / "__init__.py"
    if not init.is_file() or init.is_symlink():
        raise UpdaterError("当前插件目录缺少有效版本文件。")
    try:
        with init.open("rb") as stream:
            data = stream.read(JSON_LIMIT + 1)
    except OSError:
        raise UpdaterError("无法读取当前插件版本。") from None
    if len(data) > JSON_LIMIT:
        raise UpdaterError("当前插件版本文件异常。")
    return _python_version(data)


@contextmanager
def _update_lock(parent):
    path = parent / ".cylinder_resample-update.lock"
    marker = uuid.uuid4().hex.encode("ascii")
    try:
        descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise UpdaterError("另一次更新尚未结束。若上次 Maya 已退出，请确认后手动移除更新锁文件。") from None
    except OSError:
        raise UpdaterError("插件目录不可写，无法创建更新锁。") from None
    try:
        os.write(descriptor, marker)
        os.close(descriptor)
        descriptor = None
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            if not path.is_symlink() and path.read_bytes() == marker:
                path.unlink()
        except OSError:
            pass


def install_release(release, install_dir=None, token=None, maya_version=None):
    if not isinstance(release, dict):
        raise UpdaterError("请先检查更新，再安装正式 Release。")
    checked_year = release.get("maya_version")
    year = _maya_version(checked_year if maya_version is None else maya_version)
    if checked_year is not None and _maya_version(checked_year) != year:
        raise UpdaterError("Maya 版本与检查更新时不一致，请重新检查更新。")
    repository = _repository(release.get("repository"))
    version = release.get("version")
    _version(version)
    archive_name = "CylinderResample_Maya2024_v{}.zip".format(version)
    archive_asset = _asset(release.get("archive_asset"), repository, archive_name, ARCHIVE_LIMIT)
    manifest = _validate_manifest(release.get("manifest"), version, archive_asset, maya_version=year)
    target = _target(install_dir)
    previous_version = _installed_version(target)
    if _version(version) <= _version(previous_version):
        raise UpdaterError("新版本必须高于当前版本，不会重复安装或自动降级。")
    data = _request(archive_asset["url"], ARCHIVE_LIMIT, _token(token), "application/octet-stream")
    contents = _validated_package(data, manifest)
    config = _json(contents["config.json"])
    if config.get("repository") != repository:
        raise UpdaterError("更新包配置的仓库与 Release 不一致。")
    parent = target.parent
    staging = None
    backup = parent / ("cylinder_resample.backup-" + previous_version + "-" + uuid.uuid4().hex[:12])
    with _update_lock(parent):
        # Recheck after acquiring the lock: another updater may have won the race.
        target = _target(target)
        if _installed_version(target) != previous_version:
            raise UpdaterError("当前插件已被另一进程更新，请重新检查版本。")
        if backup.exists() or backup.is_symlink():
            raise UpdaterError("备份目录已被占用，请重新尝试更新。")
        try:
            staging = Path(tempfile.mkdtemp(prefix=".cylinder_resample.stage-", dir=str(parent)))
            for name, payload in contents.items():
                destination = staging.joinpath(*name.split("/"))
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as stream:
                    stream.write(payload)
            os.replace(str(target), str(backup))
            try:
                os.replace(str(staging), str(target))
                staging = None
            except OSError:
                if not target.exists():
                    os.replace(str(backup), str(target))
                raise
        except OSError:
            if backup.exists() and not target.exists():
                try:
                    os.replace(str(backup), str(target))
                except OSError:
                    raise UpdaterError("更新失败且自动恢复失败，原插件备份仍保留在同级 backup 目录。") from None
            raise UpdaterError("更新写入失败；原插件或其同级备份已保留，请检查目录权限。") from None
        finally:
            if staging is not None and staging.is_dir() and not staging.is_symlink():
                try:
                    shutil.rmtree(staging)
                except OSError:
                    pass
    return {
        "version": version, "previous_version": previous_version,
        "backup_dir": str(backup), "install_dir": str(target), "repository": repository,
        "maya_version": year,
    }


def rollback_install(result):
    if (not isinstance(result, dict) or not isinstance(result.get("install_dir"), str)
            or not isinstance(result.get("backup_dir"), str)):
        raise UpdaterError("更新恢复信息无效。")
    target = _target(result.get("install_dir"))
    backup = Path(result.get("backup_dir", ""))
    backup = Path(os.path.abspath(str(backup)))
    if (backup.parent != target.parent or not backup.name.startswith("cylinder_resample.backup-")
            or not backup.is_dir()):
        raise UpdaterError("原插件备份不存在或路径不安全，未修改当前插件。")
    _reject_reparse_ancestors(backup)
    if _installed_version(target) != result.get("version"):
        raise UpdaterError("当前版本已发生变化，不能恢复旧的更新记录。")
    if _installed_version(backup) != result.get("previous_version"):
        raise UpdaterError("备份版本与更新记录不一致。")
    failed = target.parent / ("cylinder_resample.failed-" + uuid.uuid4().hex[:12])
    with _update_lock(target.parent):
        target = _target(target)
        _reject_reparse_ancestors(backup)
        if (_installed_version(target) != result.get("version")
                or not backup.is_dir() or backup.is_symlink()
                or _installed_version(backup) != result.get("previous_version")):
            raise UpdaterError("当前插件或备份已发生变化，未执行恢复。")
        try:
            os.replace(str(target), str(failed))
            try:
                os.replace(str(backup), str(target))
            except OSError:
                os.replace(str(failed), str(target))
                raise
        except OSError:
            raise UpdaterError("无法自动恢复原插件；当前文件和备份均已保留。") from None
    return {"version": result["previous_version"], "install_dir": str(target), "failed_dir": str(failed)}
