from dotenv import load_dotenv
import os
from pathlib import Path
import base64
import time
import uuid
from typing import Optional

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
from app.tools import reset_verification
from app.agent import get_agent_config, update_agent_config
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

app = FastAPI(title="Bank ABC Voice Agent")



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


def _encode_audio(audio_bytes: Optional[bytes]) -> Optional[str]:
    if not audio_bytes:
        return None
    return base64.b64encode(audio_bytes).decode("utf-8")


@app.post("/call/start")
async def start_call(customer_id: str = Form("user_123")):
    session_id = _new_session(customer_id)
    reset_verification(customer_id)
    greeting = "Hello, welcome to Bank ABC. How can I help you today?"
    audio_bytes = await synthesize_audio(greeting)
    return {"session_id": session_id, "agent_response": greeting, "audio_base64": _encode_audio(audio_bytes)}


@app.post("/call/end")
async def end_call(session_id: str = Form(...)):
    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session["ended"] = True
    reset_verification(session["customer_id"])
    closing = "Thanks for calling Bank ABC. Goodbye."
    audio_bytes = await synthesize_audio(closing)
    return {"agent_response": closing, "audio_base64": _encode_audio(audio_bytes)}


@app.post("/call/turn")
async def call_turn(
    audio: UploadFile = File(...),
    session_id: str = Form(...),
    customer_id: str = Form("user_123"),
):
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

        session["messages"].append(HumanMessage(content=user_text))

        inputs = {"messages": session["messages"], "customer_id": customer_id, "flow": None}
        result = agent_app.invoke(
            inputs,
            config={
                "run_name": f"bank-abc-call-turn:{session_id}",
                "metadata": {"session_id": session_id, "customer_id": customer_id},
                "tags": ["bank-abc", "voice-agent"],
            },
        )

        bot_response = result["messages"][-1].content
        tool_calls = getattr(result["messages"][-1], "tool_calls", None) or []

        session["messages"] = result["messages"]
        session["updated_at"] = time.time()
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
async def get_session(session_id: str):
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
    return get_agent_config()


@app.put("/config")
async def write_config(payload: AgentConfigUpdate):
    return update_agent_config(base_system_prompt=payload.base_system_prompt, router_prompt=payload.router_prompt)



@app.post("/chat")
async def chat_endpoint(audio: UploadFile = File(...), customer_id: str = Form("user_123")):
    session_id = _new_session(customer_id)

if __name__ == "__main__":
    import uvicorn
    # Run with reloader
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
