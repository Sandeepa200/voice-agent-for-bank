from dotenv import load_dotenv
import os
import sys
from pathlib import Path
import base64
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional
import re

# Load env vars BEFORE importing other modules
# Explicitly point to the .env file in the backend directory
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

# Ensure backend directory is in sys.path so 'app' package can be imported
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if os.environ.get("LANGCHAIN_API_KEY") or os.environ.get("LANGSMITH_API_KEY"):
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_PROJECT", "bank-abc-voice-agent")

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Import our modules
from app.agent import app as agent_app
from app.utils import transcribe_audio, synthesize_audio
from app.tools import reset_verification, set_tool_flags, verify_identity_raw, set_verification_state
from app.agent import get_agent_config, update_agent_config
from app.db import init_db
from app.session_repo import (
    append_turn,
    create_session,
    get_session,
    set_verification,
    set_customer_id,
    get_turns,
    list_sessions,
    touch_session,
    get_turn_count,
)
from app.config_repo import ensure_seed_data, get_env_config, list_environments, update_env_config
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

def _has_valid_db_uri() -> bool:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    return bool(url and key)

async def _load_runtime_config(env_key: str) -> None:
    cfg = await get_env_config(env_key)
    defaults = get_agent_config()
    base_system_prompt = cfg.get("base_system_prompt") or defaults.get("base_system_prompt")
    router_prompt = cfg.get("router_prompt") or defaults.get("router_prompt")
    update_agent_config(base_system_prompt=base_system_prompt, router_prompt=router_prompt)
    set_tool_flags(cfg.get("tool_flags") or {})


@asynccontextmanager
async def lifespan(_: FastAPI):
    if _has_valid_db_uri():
        await init_db()
        await ensure_seed_data()
        await _load_runtime_config("dev")
    yield


app = FastAPI(title="Bank ABC Voice Agent", lifespan=lifespan, root_path=os.environ.get("VITE_API_URL", "/api"))



# CORS Configuration
# Allow all origins locally. On Vercel, trust the Vercel URL.
origins = ["*"]
vercel_url = os.environ.get("VERCEL_URL")
if vercel_url:
    origins = [f"https://{vercel_url}"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins, 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store simple session history in memory (POC Only)
# WARNING: This resets on server restart and doesn't scale.
# Use Redis for production.
SESSIONS = {}
USE_DB = _has_valid_db_uri()

# Max audio size: 10MB
MAX_FILE_SIZE = 10 * 1024 * 1024 

@app.get("/")
async def health_check():
    return {"status": "healthy", "service": "Bank ABC Voice Agent"}


def _new_session(customer_id: str) -> str:
    session_id = str(uuid.uuid4())
    SESSIONS[session_id] = {
        "session_id": session_id,
        "customer_id": customer_id,
        "created_at": time.time(),
        "updated_at": time.time(),
        "messages": [],
        "turns": [],
        "ended": False,
        "verified_identity": False,
        "verification_attempts": 0,
    }
    return session_id


async def _new_session_db(customer_id: str, env_key: str) -> str:
    session_id = str(uuid.uuid4())
    await create_session(session_id=session_id, customer_id=customer_id, env_key=env_key)
    return session_id


def _encode_audio(audio_bytes: Optional[bytes]) -> Optional[str]:
    if not audio_bytes:
        return None
    return base64.b64encode(audio_bytes).decode("utf-8")

def _sanitize_agent_text(text: str) -> str:
    cleaned = re.sub(r"<function=[^>]+>\{.*?\}", "", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()

def _sanitize_tool_calls(tool_calls: list) -> list:
    sanitized = []
    for c in tool_calls or []:
        if not isinstance(c, dict):
            continue
        out = dict(c)
        name = out.get("name")
        args = out.get("args")
        if isinstance(args, dict) and name == "verify_identity":
            redacted = dict(args)
            if "pin" in redacted:
                redacted["pin"] = "***"
            out["args"] = redacted
        sanitized.append(out)
    return sanitized

def _extract_verify_success(tool_calls: list, messages: list) -> tuple[Optional[str], int, bool]:
    calls_by_id: dict[str, dict] = {}
    attempts = 0
    for c in tool_calls or []:
        if isinstance(c, dict) and c.get("name") == "verify_identity":
            attempts += 1
            if isinstance(c.get("id"), str):
                calls_by_id[c["id"]] = c

    verified_customer_id: Optional[str] = None
    verified = False
    for m in messages or []:
        if getattr(m, "type", None) != "tool":
            continue
        if getattr(m, "name", None) != "verify_identity":
            continue
        content = (getattr(m, "content", "") or "").strip().lower()
        ok = content in {"true", "1", "yes"}
        tool_call_id = getattr(m, "tool_call_id", None)
        if ok:
            verified = True
            if isinstance(tool_call_id, str):
                call = calls_by_id.get(tool_call_id) or {}
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                cid = args.get("customer_id")
                if isinstance(cid, str) and cid.strip():
                    verified_customer_id = cid.strip()
            if not verified_customer_id:
                for c in reversed(tool_calls or []):
                    if not isinstance(c, dict) or c.get("name") != "verify_identity":
                        continue
                    args = c.get("args") if isinstance(c.get("args"), dict) else {}
                    cid = args.get("customer_id")
                    if isinstance(cid, str) and cid.strip():
                        verified_customer_id = cid.strip()
                        break
            break
    return verified_customer_id, attempts, verified


def _tool_succeeded(messages: list, tool_name: str) -> bool:
    for m in messages or []:
        if getattr(m, "type", None) != "tool":
            continue
        if getattr(m, "name", None) != tool_name:
            continue
        content = getattr(m, "content", None)
        if content is None:
            return False
        s = str(content).strip()
        if not s:
            return False
        try:
            parsed = json.loads(s)
            if isinstance(parsed, dict):
                return "error" not in parsed
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and "error" in item:
                        return False
                return True
        except Exception:
            lowered = s.lower()
            if "identity_not_verified" in lowered or "customer_not_found" in lowered or "tool_disabled" in lowered:
                return False
            if lowered.startswith("error"):
                return False
        return True
    return False


def _verification_prompt(customer_id: str) -> str:
    cid = (customer_id or "").strip().lower()
    if not cid or cid == "guest":
        return "I can help with that. What’s your Customer ID?"
    return "I can help with that. Please share your 4–6 digit PIN to verify your identity."


def _apply_sensitive_guardrail(*, agent_text: str, messages: list, customer_id: str) -> str:
    text = (agent_text or "").strip()
    if not text:
        return text
    lowered = text.lower()

    reveals_balance = bool(re.search(r"\$\s*\d", text)) or ("balance" in lowered and re.search(r"\d", text))
    reveals_contact = ("email" in lowered and "@" in text) or ("phone" in lowered and re.search(r"\+?\d", text)) or ("your name is" in lowered)

    if reveals_balance and not _tool_succeeded(messages, "get_account_balance"):
        return _verification_prompt(customer_id)
    if reveals_contact and not _tool_succeeded(messages, "get_customer_profile"):
        return _verification_prompt(customer_id)
    return text


def _is_rate_limited_error(exc: Exception) -> bool:
    s = str(exc).lower()
    return "error code: 429" in s or "rate_limit_exceeded" in s or "rate limit reached" in s


def _extract_retry_after_seconds(exc: Exception) -> Optional[int]:
    s = str(exc)
    m = re.search(r"try again in\s+(\d+)m([\d.]+)s", s, flags=re.IGNORECASE)
    if not m:
        return None
    minutes = int(m.group(1))
    seconds = float(m.group(2))
    return int(max(0, minutes * 60 + seconds))


@app.post("/call/start")
async def start_call(env_key: str = Form("dev")):
    customer_id = "guest"
    session_id = await _new_session_db(customer_id, env_key) if USE_DB else _new_session(customer_id)
    reset_verification(customer_id)
    if USE_DB:
        await _load_runtime_config(env_key)
    greeting = "Hello, welcome to Bank ABC. How can I help you today?"
    audio_bytes = await synthesize_audio(greeting)
    if USE_DB:
        await set_verification(session_id, verified_identity=False, verification_attempts=0)
        await append_turn(session_id=session_id, ts=time.time(), user_transcript=None, agent_response=greeting, tool_calls=[])
    return {"session_id": session_id, "agent_response": greeting, "audio_base64": _encode_audio(audio_bytes), "is_verified": False}


@app.post("/call/end")
async def end_call(session_id: str = Form(...)):
    if USE_DB:
        session = await get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        await touch_session(session_id, ended=True)
        reset_verification(session["customer_id"])
    else:
        session = SESSIONS.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        session["ended"] = True
        reset_verification(session["customer_id"])
    closing = "Thanks for calling Bank ABC. Goodbye."
    audio_bytes = await synthesize_audio(closing)
    if USE_DB:
        await append_turn(session_id=session_id, ts=time.time(), user_transcript=None, agent_response=closing, tool_calls=[])
    return {"agent_response": closing, "audio_base64": _encode_audio(audio_bytes), "is_verified": False}


@app.post("/call/turn")
async def call_turn(
    audio: UploadFile = File(...),
    session_id: str = Form(...),
):
    if USE_DB:
        session = await get_session(session_id)
        if not session or session.get("ended"):
            raise HTTPException(status_code=404, detail="Session not found or ended")
        env_key = session.get("env_key") or "dev"
        await _load_runtime_config(env_key)
    else:
        session = SESSIONS.get(session_id)
        if not session or session.get("ended"):
            raise HTTPException(status_code=404, detail="Session not found or ended")
            
    # Hydrate in-memory verification cache from persistent session state
    current_customer_id = session.get("customer_id") or "guest"
    is_verified_session = bool(session.get("verified_identity"))
    set_verification_state(current_customer_id, is_verified_session)

    try:
        audio_content = await audio.read()

        if len(audio_content) < 1024:
            return JSONResponse(content={"error": "Audio recording too short. Please speak longer."}, status_code=400)

        if len(audio_content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Audio file too large (Max 10MB)",
            )

        user_text = await transcribe_audio(audio_content)
        if not user_text:
            return JSONResponse(
                content={"error": "Could not transcribe audio. Please speak clearly and ensure the recording is long enough."},
                status_code=400,
            )

        if USE_DB:
            turns = await get_turns(session_id)
            messages = []
            for t in turns:
                if t.get("user_transcript"):
                    messages.append(HumanMessage(content=t["user_transcript"]))
                if t.get("agent_response"):
                    messages.append(AIMessage(content=t["agent_response"]))
            messages.append(HumanMessage(content=user_text))
        else:
            session["messages"].append(HumanMessage(content=user_text))
            messages = session["messages"]

        current_customer_id = session.get("customer_id") or "guest"
        inputs = {"messages": messages, "customer_id": current_customer_id, "flow": None}
        try:
            result = agent_app.invoke(
                inputs,
                config={
                    "run_name": f"bank-abc-call-turn:{session_id}",
                    "metadata": {"session_id": session_id, "customer_id": current_customer_id},
                    "tags": ["bank-abc", "voice-agent"],
                },
            )
        except Exception as e:
            if _is_rate_limited_error(e):
                retry_after = _extract_retry_after_seconds(e)
                headers = {}
                if retry_after is not None:
                    headers["Retry-After"] = str(retry_after)
                raise HTTPException(status_code=429, detail="LLM rate limit reached. Please try again shortly.", headers=headers)
            raise

        bot_response = _sanitize_agent_text(result["messages"][-1].content or "")
        all_tool_calls = []
        for m in result.get("messages") or []:
            tcs = getattr(m, "tool_calls", None) or []
            if isinstance(tcs, list) and tcs:
                all_tool_calls.extend(tcs)
        verified_customer_id, attempts_delta, verified_now = _extract_verify_success(all_tool_calls, result.get("messages") or [])
        tool_calls = _sanitize_tool_calls(all_tool_calls)
        bot_response = _apply_sensitive_guardrail(agent_text=bot_response, messages=result.get("messages") or [], customer_id=current_customer_id)

        now = time.time()
        if USE_DB:
            await append_turn(session_id=session_id, ts=now, user_transcript=user_text, agent_response=bot_response, tool_calls=tool_calls)
            if attempts_delta or verified_now:
                next_attempts = int(session.get("verification_attempts") or 0) + int(attempts_delta)
                await set_verification(session_id, verified_identity=bool(verified_now or session.get("verified_identity")), verification_attempts=next_attempts)
            if verified_now and verified_customer_id:
                await set_customer_id(session_id, customer_id=verified_customer_id)
        else:
            session["messages"] = result["messages"]
            session["updated_at"] = now
            if attempts_delta:
                session["verification_attempts"] = int(session.get("verification_attempts") or 0) + int(attempts_delta)
            if verified_now:
                session["verified_identity"] = True
            if verified_now and verified_customer_id:
                session["customer_id"] = verified_customer_id
            session["turns"].append(
                {
                    "ts": session["updated_at"],
                    "user_transcript": user_text,
                    "agent_response": bot_response,
                    "tool_calls": tool_calls,
                }
            )

        # Determine final verification state to return to UI
        final_is_verified = verified_now or is_verified_session
        
        audio_bytes = await synthesize_audio(bot_response)
        return {
            "user_transcript": user_text, 
            "agent_response": bot_response, 
            "audio_base64": _encode_audio(audio_bytes),
            "is_verified": final_is_verified
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"Server Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sessions")
async def list_sessions_endpoint():
    if USE_DB:
        sessions = await list_sessions()
        turns_counts = {}
        for s in sessions:
            turns_counts[s["session_id"]] = await get_turn_count(s["session_id"])
        items = []
        for s in sessions:
            items.append(
                {
                    "session_id": s["session_id"],
                    "customer_id": s["customer_id"],
                    "created_at": s["created_at"],
                    "updated_at": s["updated_at"],
                    "turns": turns_counts.get(s["session_id"], 0),
                    "ended": bool(s.get("ended")),
                }
            )
        return {"sessions": items}
    items = []
    for s in SESSIONS.values():
        items.append(
            {
                "session_id": s["session_id"],
                "customer_id": s["customer_id"],
                "created_at": s["created_at"],
                "updated_at": s["updated_at"],
                "turns": len(s.get("turns") or []),
                "ended": bool(s.get("ended")),
            }
        )
    items.sort(key=lambda x: x["updated_at"], reverse=True)
    return {"sessions": items}


@app.get("/sessions/{session_id}")
async def get_session_endpoint(session_id: str):
    if USE_DB:
        session = await get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        turns = await get_turns(session_id)
        return {
            "session_id": session["session_id"],
            "customer_id": session["customer_id"],
            "created_at": session["created_at"],
            "updated_at": session["updated_at"],
            "ended": bool(session.get("ended")),
            "turns": turns,
        }
    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session["session_id"],
        "customer_id": session["customer_id"],
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "ended": bool(session.get("ended")),
        "turns": session.get("turns") or [],
    }


class AgentConfigUpdate(BaseModel):
    base_system_prompt: Optional[str] = None
    router_prompt: Optional[str] = None


@app.get("/config")
async def read_config():
    if USE_DB:
        cfg = await get_env_config("dev")
        defaults = get_agent_config()
        return {
            "base_system_prompt": cfg.get("base_system_prompt") or defaults.get("base_system_prompt"),
            "router_prompt": cfg.get("router_prompt") or defaults.get("router_prompt"),
        }
    return get_agent_config()


@app.put("/config")
async def write_config(payload: AgentConfigUpdate):
    if USE_DB:
        cfg = await update_env_config("dev", base_system_prompt=payload.base_system_prompt, router_prompt=payload.router_prompt)
        if cfg.get("base_system_prompt") is not None or cfg.get("router_prompt") is not None:
            update_agent_config(base_system_prompt=cfg.get("base_system_prompt"), router_prompt=cfg.get("router_prompt"))
        return {"base_system_prompt": cfg.get("base_system_prompt"), "router_prompt": cfg.get("router_prompt")}
    return update_agent_config(base_system_prompt=payload.base_system_prompt, router_prompt=payload.router_prompt)


class ToolsUpdate(BaseModel):
    tool_flags: dict


class RoutingRulesUpdate(BaseModel):
    routing_rules: dict


@app.get("/admin/environments")
async def admin_list_environments():
    if not USE_DB:
        return {"environments": [{"key": "dev", "name": "Development"}]}
    return {"environments": await list_environments()}


@app.get("/admin/config")
async def admin_get_config(env: str = "dev"):
    if not USE_DB:
        return get_agent_config()
    cfg = await get_env_config(env)
    defaults = get_agent_config()
    return {
        "env_key": env,
        "base_system_prompt": cfg.get("base_system_prompt") or defaults.get("base_system_prompt"),
        "router_prompt": cfg.get("router_prompt") or defaults.get("router_prompt"),
        "tool_flags": cfg.get("tool_flags") or {},
        "routing_rules": cfg.get("routing_rules") or {},
        "updated_at": cfg.get("updated_at"),
    }


@app.put("/admin/config")
async def admin_put_config(payload: AgentConfigUpdate, env: str = "dev"):
    if not USE_DB:
        return update_agent_config(base_system_prompt=payload.base_system_prompt, router_prompt=payload.router_prompt)
    cfg = await update_env_config(env, base_system_prompt=payload.base_system_prompt, router_prompt=payload.router_prompt)
    if env == "dev":
        if cfg.get("base_system_prompt") is not None or cfg.get("router_prompt") is not None:
            update_agent_config(base_system_prompt=cfg.get("base_system_prompt"), router_prompt=cfg.get("router_prompt"))
    return {
        "env_key": env,
        "base_system_prompt": cfg.get("base_system_prompt"),
        "router_prompt": cfg.get("router_prompt"),
        "updated_at": cfg.get("updated_at"),
    }


@app.get("/admin/tools")
async def admin_get_tools(env: str = "dev"):
    if not USE_DB:
        return {"env_key": env, "tool_flags": {}}
    cfg = await get_env_config(env)
    return {"env_key": env, "tool_flags": cfg.get("tool_flags") or {}, "updated_at": cfg.get("updated_at")}


@app.put("/admin/tools")
async def admin_put_tools(payload: ToolsUpdate, env: str = "dev"):
    if not USE_DB:
        return {"env_key": env, "tool_flags": payload.tool_flags}
    cfg = await update_env_config(env, tool_flags=payload.tool_flags or {})
    if env == "dev":
        set_tool_flags(cfg.get("tool_flags") or {})
    return {"env_key": env, "tool_flags": cfg.get("tool_flags") or {}, "updated_at": cfg.get("updated_at")}


@app.get("/admin/routing")
async def admin_get_routing(env: str = "dev"):
    if not USE_DB:
        return {"env_key": env, "routing_rules": {}}
    cfg = await get_env_config(env)
    return {"env_key": env, "routing_rules": cfg.get("routing_rules") or {}, "updated_at": cfg.get("updated_at")}


@app.put("/admin/routing")
async def admin_put_routing(payload: RoutingRulesUpdate, env: str = "dev"):
    if not USE_DB:
        return {"env_key": env, "routing_rules": payload.routing_rules}
    cfg = await update_env_config(env, routing_rules=payload.routing_rules or {})
    return {"env_key": env, "routing_rules": cfg.get("routing_rules") or {}, "updated_at": cfg.get("updated_at")}



@app.post("/chat")
async def chat_endpoint(audio: UploadFile = File(...), customer_id: str = Form("John123")):
    session_id = await _new_session_db(customer_id, "dev") if USE_DB else _new_session(customer_id)
    return {"session_id": session_id, **(await call_turn(audio=audio, session_id=session_id))}

if __name__ == "__main__":
    import uvicorn
    # Run with reloader
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
