import os
from typing import Optional
from supabase import create_client, Client

_client: Optional[Client] = None

def get_supabase_client() -> Client:
    global _client
    if _client is None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL or SUPABASE_SERVICE_KEY is not set")
        _client = create_client(url, key)
    return _client


async def init_db() -> None:
    # No-op for Supabase as tables are created via SQL migrations
    pass
