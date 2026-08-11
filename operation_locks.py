"""In-process locks for sensitive per-user operations."""
import asyncio
from collections import defaultdict

_user_locks = defaultdict(asyncio.Lock)


def user_operation_lock(user_id: int) -> asyncio.Lock:
    return _user_locks[int(user_id)]
