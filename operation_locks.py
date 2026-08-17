"""In-process locks for sensitive operations."""
import asyncio
from collections import defaultdict

_user_locks = defaultdict(asyncio.Lock)
_service_creation_locks = defaultdict(asyncio.Lock)


def user_operation_lock(user_id: int) -> asyncio.Lock:
    return _user_locks[int(user_id)]


def service_creation_lock(panel_id: int, category_prefix: str, volume_tag: str) -> asyncio.Lock:
    """Serialize config allocation/creation for the same panel + name prefix."""
    key = (int(panel_id), str(category_prefix), str(volume_tag))
    return _service_creation_locks[key]
