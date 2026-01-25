import time
from typing import Any, Dict, Optional

from app.db import get_db


DEFAULT_ENV_KEY = "dev"


async def ensure_seed_data() -> None:
    await ensure_env_config(DEFAULT_ENV_KEY)


async def ensure_env_config(env_key: str) -> None:
    db = get_db()
    doc = await db["configs"].find_one({"env_key": env_key})
    if doc:
        return
    now = time.time()
    await db["configs"].insert_one(
        {
            "env_key": env_key,
            "base_system_prompt": None,
            "router_prompt": None,
            "tool_flags": {},
            "routing_rules": {},
            "updated_at": now,
        }
    )


async def list_environments() -> list[Dict[str, Any]]:
    return [{"key": DEFAULT_ENV_KEY, "name": "Development"}]


async def get_env_config(env_key: str) -> Dict[str, Any]:
    db = get_db()
    await ensure_env_config(env_key)
    doc = await db["configs"].find_one({"env_key": env_key}, {"_id": 0})
    return doc or {"env_key": env_key}


async def update_env_config(
    env_key: str,
    *,
    base_system_prompt: Optional[str] = None,
    router_prompt: Optional[str] = None,
    tool_flags: Optional[Dict[str, Any]] = None,
    routing_rules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    db = get_db()
    await ensure_env_config(env_key)
    update: Dict[str, Any] = {"updated_at": time.time()}
    if base_system_prompt is not None:
        update["base_system_prompt"] = base_system_prompt
    if router_prompt is not None:
        update["router_prompt"] = router_prompt
    if tool_flags is not None:
        update["tool_flags"] = tool_flags
    if routing_rules is not None:
        update["routing_rules"] = routing_rules
    await db["configs"].update_one({"env_key": env_key}, {"$set": update})
    return await get_env_config(env_key)
