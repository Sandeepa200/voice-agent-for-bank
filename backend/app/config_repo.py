import time
from typing import Any, Dict, Optional
from starlette.concurrency import run_in_threadpool
from app.db import get_supabase_client


DEFAULT_ENV_KEY = "dev"


async def ensure_seed_data() -> None:
    await ensure_env_config(DEFAULT_ENV_KEY)


async def ensure_env_config(env_key: str) -> None:
    db = get_supabase_client()
    resp = await run_in_threadpool(lambda: db.table("configs").select("*").eq("env_key", env_key).execute())
    if resp.data and len(resp.data) > 0:
        return
    now = time.time()
    data = {
        "env_key": env_key,
        "base_system_prompt": None,
        "router_prompt": None,
        "tool_flags": {},
        "routing_rules": {},
        "updated_at": now,
    }
    try:
        await run_in_threadpool(lambda: db.table("configs").insert(data).execute())
    except Exception:
        data.pop("updated_at", None)
        await run_in_threadpool(lambda: db.table("configs").insert(data).execute())


async def list_environments() -> list[Dict[str, Any]]:
    return [{"key": DEFAULT_ENV_KEY, "name": "Development"}]


async def get_env_config(env_key: str) -> Dict[str, Any]:
    db = get_supabase_client()
    await ensure_env_config(env_key)
    resp = await run_in_threadpool(lambda: db.table("configs").select("*").eq("env_key", env_key).execute())
    if resp.data and len(resp.data) > 0:
        return resp.data[0]
    return {"env_key": env_key}


async def update_env_config(
    env_key: str,
    *,
    base_system_prompt: Optional[str] = None,
    router_prompt: Optional[str] = None,
    tool_flags: Optional[Dict[str, Any]] = None,
    routing_rules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    db = get_supabase_client()
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
    
    try:
        await run_in_threadpool(lambda: db.table("configs").update(update).eq("env_key", env_key).execute())
    except Exception:
        update.pop("updated_at", None)
        await run_in_threadpool(lambda: db.table("configs").update(update).eq("env_key", env_key).execute())
    
    return await get_env_config(env_key)
