"""Profile 生命周期与低风险磁盘维护工具。

只清理 Firefox/Camoufox 可重建的缓存和诊断目录；登录态文件、Cookie、TOTP
以及 auth/chatgpt 的站点存储默认保留。完整 profile 的裁剪由调用方显式选择。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable

from ..config import settings


RUNTIME_CACHE_DIRS = (
    "cache2",
    "startupCache",
    "crashes",
    "minidumps",
    "thumbnails",
    "safebrowsing",
    "datareporting",
    "extension-store",
    "extension-store-menus",
    "sessionstore-backups",
    "bookmarkbackups",
)

# 这些文件是浏览器启动时重建的运行时锁，不应被复制或长期保留。
RUNTIME_LOCK_FILES = ("parent.lock", ".parentlock", "lock", "SingletonLock", "SingletonSocket", "SingletonCookie")


def profiles_root() -> Path:
    return Path(settings.profiles_dir).expanduser().resolve()


def _safe_profile_path(profile_path: str | Path) -> Path:
    path = Path(profile_path).expanduser().resolve()
    root = profiles_root()
    if path == root or root not in path.parents:
        raise ValueError("拒绝操作 profiles 目录之外的路径")
    return path


def _remove_path(path: Path) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        return False
    return not path.exists()


def cleanup_profile_runtime_artifacts(profile_path: str | Path) -> dict[str, int]:
    """删除可重建的 Firefox 缓存/诊断目录，不触碰站点登录态。"""
    path = _safe_profile_path(profile_path)
    removed = 0
    failed = 0
    for name in RUNTIME_CACHE_DIRS:
        target = path / name
        if target.exists() or target.is_symlink():
            if _remove_path(target):
                removed += 1
            else:
                failed += 1
    for name in RUNTIME_LOCK_FILES:
        target = path / name
        if target.exists() or target.is_symlink():
            if _remove_path(target):
                removed += 1
            else:
                failed += 1
    return {"removed": removed, "failed": failed}


def remove_profile_tree(profile_path: str | Path) -> bool:
    """安全删除一个 profile；仅允许 profiles 根目录下的子目录。"""
    path = _safe_profile_path(profile_path)
    if not path.exists():
        return True
    return _remove_path(path)


def profile_has_runtime_lock(profile_path: str | Path) -> bool:
    """Return whether Firefox currently appears to own this persistent profile."""
    path = _safe_profile_path(profile_path)
    return any((path / name).exists() for name in RUNTIME_LOCK_FILES)


def compact_profile(profile_path: str | Path) -> dict[str, int]:
    """把 profile 裁剪为可复用的轻量登录态 profile。

    保留 cookies/key/prefs 以及 auth.openai.com、chatgpt.com 的站点存储；
    删除扩展 IndexedDB、历史记录和其它非认证数据。该操作不删除完整 profile
    目录，也不改动 Cookie/认证站点存储。
    """
    path = _safe_profile_path(profile_path)
    result = cleanup_profile_runtime_artifacts(path)
    removed = result["removed"]
    failed = result["failed"]

    for name in ("places.sqlite", "places.sqlite-wal", "places.sqlite-shm", "favicons.sqlite", "favicons.sqlite-wal", "favicons.sqlite-shm"):
        target = path / name
        if target.exists() or target.is_symlink():
            if _remove_path(target):
                removed += 1
            else:
                failed += 1

    storage = path / "storage"
    default_storage = storage / "default"
    if default_storage.exists():
        for child in default_storage.iterdir():
            name = child.name.lower()
            # 保留认证站点 storage；扩展/挑战缓存不属于登录态。
            if "auth.openai.com" in name or "chatgpt.com" in name:
                continue
            if _remove_path(child):
                removed += 1
            else:
                failed += 1
    for target in (storage / "permanent" / "chrome", storage / "ls-archive.sqlite"):
        if target.exists() or target.is_symlink():
            if _remove_path(target):
                removed += 1
            else:
                failed += 1
    return {"removed": removed, "failed": failed}


def compact_profiles(profile_paths: Iterable[str | Path]) -> dict[str, int]:
    summary = {"profiles": 0, "removed": 0, "failed": 0}
    for profile_path in profile_paths:
        try:
            result = compact_profile(profile_path)
        except (OSError, ValueError):
            summary["failed"] += 1
            continue
        summary["profiles"] += 1
        summary["removed"] += result["removed"]
        summary["failed"] += result["failed"]
    return summary
