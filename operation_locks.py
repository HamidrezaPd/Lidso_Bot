import asyncio
from collections import defaultdict

_user_locks = defaultdict(asyncio.Lock)
_service_locks = defaultdict(asyncio.Lock)

def user_operation_lock(user_id: int):
    return _user_locks[int(user_id)]

def service_creation_lock(panel_id: int, category_prefix: str, volume_tag: str):
    return _service_locks[(int(panel_id), str(category_prefix), str(volume_tag))]
