from dotenv import load_dotenv
import os
from pathlib import Path
import base64
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional
import re

# Load env vars BEFORE importing other modules
# Explicitly point to the .env file in the backend directory
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

if os.environ.get("LANGCHAIN_API_KEY") or os.environ.get("LANGSMITH_API_KEY"):
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_PROJECT", "bank-abc-voice-agent")

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Import our modules
from app.agent import app as agent_app
from app.utils import transcribe_audio, synthesize_audio
from app.tools import reset_verification, set_tool_flags, verify_identity_raw
from app.agent import get_agent_config, update_agent_config
from app.db import init_db, get_mongo_client, get_db
from app.session_repo import (
    append_turn as mongo_append_turn,
    create_session as mongo_create_session,
    get_session as mongo_get_session,
    set_verification as mongo_set_verification,
    get_turns as mongo_get_turns,
    list_sessions as mongo_list_sessions,
    touch_session as mongo_touch_session,
)
from app.config_repo import ensure_seed_data, get_env_config, list_environments, update_env_config
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

@asynccontextmanager
async def lifespan(_: FastAPI):
    if os.environ.get("MONGODB_URI"):
        await init_db()
        await ensure_seed_data()
        cfg = await get_env_config("dev")
        set_tool_flags(cfg.get("tool_flags") or {})
    yield
    if os.environ.get("MONGODB_URI"):
        get_mongo_client().close()


app = FastAPI(title="Bank ABC Voice Agent", lifespan=lifespan)



# CORS Configuration
# In production, replace ["*"] with ["https://your-frontend.vercel.app"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store simple session history in memory (POC Only)
# WARNING: This resets on server restart and doesn't scale.
# Use Redis for production.
SESSIONS = {}
USE_MONGO = bool(os.environ.get("MONGODB_URI"))

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
    }
    return session_id


async def _new_session_mongo(customer_id: str, env_key: str) -> str:
    session_id = str(uuid.uuid4())
    await mongo_create_session(session_id=session_id, customer_id=customer_id, env_key=env_key)
    return session_id


def _encode_audio(audio_bytes: Optional[bytes]) -> Optional[str]:
    if not audio_bytes:
        return None
    return base64.b64encode(audio_bytes).decode("utf-8")

def _sanitize_agent_text(text: str) -> str:
    cleaned = re.sub(r"<function=[^>]+>\{.*?\}", "", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


@app.post("/call/start")
async def start_call(customer_id: str = Form("user_123"), env_key: str = Form("dev"), pin: Optional[str] = Form(None)):
    session_id = await _new_session_mongo(customer_id, env_key) if USE_MONGO else _new_session(customer_id)
    verified = False
    attempts = 0
    if pin is not None and str(pin).strip() != "":
        attempts = 1
        verified = verify_identity_raw(customer_id, str(pin).strip())
        if not verified:
            if USE_MONGO:
                await mongo_set_verification(session_id, verified_identity=False, verification_attempts=attempts)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="verify_identity_failed")
    else:
        reset_verification(customer_id)
    greeting = "Hello, welcome to Bank ABC. How can I help you today?"
    audio_bytes = await synthesize_audio(greeting)
    if USE_MONGO:
        await mongo_set_verification(session_id, verified_identity=verified, verification_attempts=attempts)
        await mongo_append_turn(session_id=session_id, ts=time.time(), user_transcript=None, agent_response=greeting, tool_calls=[])
    return {"session_id": session_id, "agent_response": greeting, "audio_base64": _encode_audio(audio_bytes)}


@app.post("/call/end")
async def end_call(session_id: str = Form(...)):
    if USE_MONGO:
        session = await mongo_get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        await mongo_touch_session(session_id, ended=True)
        reset_verification(session["customer_id"])
    else:
        session = SESSIONS.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        session["ended"] = True
        reset_verification(session["customer_id"])
    closing = "Thanks for calling Bank ABC. Goodbye."
    audio_bytes = await synthesize_audio(closing)
    if USE_MONGO:
        await mongo_append_turn(session_id=session_id, ts=time.time(), user_transcript=None, agent_response=closing, tool_calls=[])
    return {"agent_response": closing, "audio_base64": _encode_audio(audio_bytes)}


@app.post("/call/turn")
async def call_turn(
    audio: UploadFile = File(...),
    session_id: str = Form(...),
    customer_id: str = Form("user_123"),
    env_key: str = Form("dev"),
):
    if USE_MONGO:
        session = await mongo_get_session(session_id)
        if not session or session.get("ended"):
            raise HTTPException(status_code=404, detail="Session not found or ended")
        if session["customer_id"] != customer_id:
            raise HTTPException(status_code=400, detail="customer_id does not match session")
    else:
        session = SESSIONS.get(session_id)
        if not session or session.get("ended"):
            raise HTTPException(status_code=404, detail="Session not found or ended")
        if session["customer_id"] != customer_id:
            raise HTTPException(status_code=400, detail="customer_id does not match session")

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

        if USE_MONGO:
            turns = await mongo_get_turns(session_id)
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

        inputs = {"messages": messages, "customer_id": customer_id, "flow": None}
        result = agent_app.invoke(
            inputs,
            config={
                "run_name": f"bank-abc-call-turn:{session_id}",
                "metadata": {"session_id": session_id, "customer_id": customer_id},
                "tags": ["bank-abc", "voice-agent"],
            },
        )

        bot_response = _sanitize_agent_text(result["messages"][-1].content or "")
        tool_calls = getattr(result["messages"][-1], "tool_calls", None) or []

        now = time.time()
        if USE_MONGO:
            await mongo_append_turn(session_id=session_id, ts=now, user_transcript=user_text, agent_response=bot_response, tool_calls=tool_calls)
        else:
            session["messages"] = result["messages"]
            session["updated_at"] = now
            session["turns"].append(
                {
                    "ts": session["updated_at"],
                    "user_transcript": user_text,
                    "agent_response": bot_response,
                    "tool_calls": tool_calls,
                }
            )

        audio_bytes = await synthesize_audio(bot_response)
        return {"user_transcript": user_text, "agent_response": bot_response, "audio_base64": _encode_audio(audio_bytes)}

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"Server Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sessions")
async def list_sessions():
    if USE_MONGO:
        sessions = await mongo_list_sessions()
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


async def get_turn_count(session_id: str) -> int:
    if not USE_MONGO:
        return len(SESSIONS.get(session_id, {}).get("turns") or [])
    return await get_db()["call_turns"].count_documents({"session_id": session_id})


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    if USE_MONGO:
        session = await mongo_get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        turns = await mongo_get_turns(session_id)
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
    if USE_MONGO:
        cfg = await get_env_config("dev")
        defaults = get_agent_config()
        return {
            "base_system_prompt": cfg.get("base_system_prompt") or defaults.get("base_system_prompt"),
            "router_prompt": cfg.get("router_prompt") or defaults.get("router_prompt"),
        }
    return get_agent_config()


@app.put("/config")
async def write_config(payload: AgentConfigUpdate):
    if USE_MONGO:
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
    if not USE_MONGO:
        return {"environments": [{"key": "dev", "name": "Development"}]}
    return {"environments": await list_environments()}


@app.get("/admin/config")
async def admin_get_config(env: str = "dev"):
    if not USE_MONGO:
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
    if not USE_MONGO:
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
    if not USE_MONGO:
        return {"env_key": env, "tool_flags": {}}
    cfg = await get_env_config(env)
    return {"env_key": env, "tool_flags": cfg.get("tool_flags") or {}, "updated_at": cfg.get("updated_at")}


@app.put("/admin/tools")
async def admin_put_tools(payload: ToolsUpdate, env: str = "dev"):
    if not USE_MONGO:
        return {"env_key": env, "tool_flags": payload.tool_flags}
    cfg = await update_env_config(env, tool_flags=payload.tool_flags or {})
    if env == "dev":
        set_tool_flags(cfg.get("tool_flags") or {})
    return {"env_key": env, "tool_flags": cfg.get("tool_flags") or {}, "updated_at": cfg.get("updated_at")}


@app.get("/admin/routing")
async def admin_get_routing(env: str = "dev"):
    if not USE_MONGO:
        return {"env_key": env, "routing_rules": {}}
    cfg = await get_env_config(env)
    return {"env_key": env, "routing_rules": cfg.get("routing_rules") or {}, "updated_at": cfg.get("updated_at")}


@app.put("/admin/routing")
async def admin_put_routing(payload: RoutingRulesUpdate, env: str = "dev"):
    if not USE_MONGO:
        return {"env_key": env, "routing_rules": payload.routing_rules}
    cfg = await update_env_config(env, routing_rules=payload.routing_rules or {})
    return {"env_key": env, "routing_rules": cfg.get("routing_rules") or {}, "updated_at": cfg.get("updated_at")}



@app.post("/chat")
async def chat_endpoint(audio: UploadFile = File(...), customer_id: str = Form("user_123")):
    session_id = await _new_session_mongo(customer_id, "dev") if USE_MONGO else _new_session(customer_id)
    return {"session_id": session_id, **(await call_turn(audio=audio, session_id=session_id, customer_id=customer_id))}

if __name__ == "__main__":
    import uvicorn
    # Run with reloader
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
