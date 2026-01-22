from dotenv import load_dotenv
import os
from pathlib import Path

# Load env vars BEFORE importing other modules
# Explicitly point to the .env file in the backend directory
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

from fastapi import FastAPI, UploadFile, File, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import base64
import uuid

# Import our modules
# Use relative imports if running as package, or absolute if running script
try:
    from backend.app.agent import app as agent_app
    from backend.app.utils import transcribe_audio, synthesize_audio
except ImportError:
    from app.agent import app as agent_app
    from app.utils import transcribe_audio, synthesize_audio

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

@app.post("/chat")
async def chat_endpoint(
    audio: UploadFile = File(...), 
    customer_id: str = "user_123" 
):
    try:
        # 1. Validate File Size (Basic Check)
        # Note: audio.file.read() is blocking, but for small files ok.
        # Better to check Content-Length header if available, but clients often omit it.
        # We'll read it into memory since we need to send it to Groq anyway.
        audio_content = await audio.read()
        
        # Check for empty or extremely short files
        if len(audio_content) < 1024: # Less than 1KB is likely invalid or empty
             return JSONResponse(
                content={"error": "Audio recording too short. Please speak longer."}, 
                status_code=400
            )

        if len(audio_content) > MAX_FILE_SIZE:
             raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Audio file too large (Max 10MB)"
            )
        
        # 2. Transcribe (STT)
        user_text = await transcribe_audio(audio_content)
        if not user_text:
            return JSONResponse(
                content={"error": "Could not transcribe audio. Please speak clearly and ensure the recording is long enough."}, 
                status_code=400
            )
        
        print(f"User ({customer_id}): {user_text}")

        # 3. Agent Reasoning (LangGraph)
        if customer_id not in SESSIONS:
            SESSIONS[customer_id] = []
        
        # Append user message
        SESSIONS[customer_id].append(("user", user_text))
        
        # Invoke Agent
        inputs = {
            "messages": SESSIONS[customer_id],
            "customer_id": customer_id
        }
        
        # Run the graph
        result = agent_app.invoke(inputs)
        
        # Get latest agent response
        # LangGraph returns a list of messages. The last one is the AI response.
        bot_response = result["messages"][-1].content
        
        # Update Session History
        SESSIONS[customer_id] = result["messages"]
        
        print(f"Agent: {bot_response}")

        # 4. Synthesize (TTS)
        audio_bytes = await synthesize_audio(bot_response)
        if not audio_bytes:
             # Fallback if TTS fails: Return text but no audio
             return {
                "user_transcript": user_text,
                "agent_response": bot_response,
                "audio_base64": None
            }

        audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")

        return {
            "user_transcript": user_text,
            "agent_response": bot_response,
            "audio_base64": audio_base64
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"Server Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    # Run with reloader
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
