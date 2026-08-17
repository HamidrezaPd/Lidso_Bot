"""Cross-process locks for sensitive operations.

The previous implementation used only asyncio.Lock. That protects concurrent
requests inside one Python process, but it does NOT protect the same operation
when the bot is accidentally started twice or multiple worker processes are
running on the same server.

Service creation is especially sensitive because the flow is:
    choose next config name -> create account on panel -> save order

This module keeps the fast in-process lock and adds a filesystem lock, so the
whole allocation/creation flow is serialized across processes on the same
machine. The OS releases the filesystem lock automatically if a process dies.
"""

import asyncio
import hashlib
import os
from collections import defaultdict
from pathlib import Path

from filelock import FileLock


_user_locks = defaultdict(asyncio.Lock)
_service_creation_locks = defaultdict(asyncio.Lock)

_LOCK_DIR = Path(os.getenv("LIDSO_LOCK_DIR", ".locks"))


def user_operation_lock(user_id: int) -> asyncio.Lock:
    """Serialize sensitive operations for one Telegram user in this process."""
    return _user_locks[int(user_id)]


def _service_lock_path(panel_id: int, category_prefix: str, volume_tag: str) -> Path:
    raw_key = f"{int(panel_id)}|{category_prefix}|{volume_tag}"
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:32]
    return _LOCK_DIR / f"service_creation_{digest}.lock"


class _ServiceCreationLock:
    """Async context manager combining process-local and cross-process locks."""

    def __init__(self, panel_id: int, category_prefix: str, volume_tag: str):
        self._key = (int(panel_id), str(category_prefix), str(volume_tag))
        self._local_lock = _service_creation_locks[self._key]
        self._file_lock = None

    async def __aenter__(self):
        # First serialize tasks inside this Python process.
        await self._local_lock.acquire()

        try:
            _LOCK_DIR.mkdir(parents=True, exist_ok=True)
            path = _service_lock_path(*self._key)
            self._file_lock = FileLock(str(path), timeout=-1)

            # FileLock.acquire() is blocking, so never block the bot's event loop.
            await asyncio.to_thread(self._file_lock.acquire)
            return self
        except Exception:
            self._local_lock.release()
            raise

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if self._file_lock is not None:
                await asyncio.to_thread(self._file_lock.release)
        finally:
            self._local_lock.release()


def service_creation_lock(panel_id: int, category_prefix: str, volume_tag: str) -> _ServiceCreationLock:
    """Serialize service name allocation + panel creation across processes.

    The caller must keep this lock around the ENTIRE critical section, including
    get_next_config_name(), create_panel_account(), subscription merging and the
    final ServiceOrder commit.
    """
    return _ServiceCreationLock(panel_id, category_prefix, volume_tag)
