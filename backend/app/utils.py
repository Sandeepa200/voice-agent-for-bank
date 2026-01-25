import os
import httpx
from groq import Groq

# Initialize Groq Client
# Note: We initialize this lazily or globally depending on preference, 
# but here globally is fine as long as env vars are loaded.
groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

async def transcribe_audio(audio_bytes: bytes) -> str:
    """
    Transcribes audio using Groq's Distil-Whisper model.
    """
    try:
        filename = "audio"
        if audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
            filename = "audio.wav"
        elif audio_bytes[:4] == b"\x1aE\xdf\xa3":
            filename = "audio.webm"
        elif audio_bytes[:3] == b"ID3":
            filename = "audio.mp3"

        transcription = groq_client.audio.transcriptions.create(
            file=(filename, audio_bytes),
            model="whisper-large-v3",
            response_format="json",
            language="en",
            temperature=0.0
        )
        return transcription.text
    except Exception as e:
        print(f"STT Error: {e}")
        # In production, you might want to raise this to alert the user
        return ""

async def synthesize_audio(text: str) -> bytes:
    """
    Synthesizes speech using Deepgram's Aura model via raw HTTP API.
    This is often simpler for server-to-server memory streaming than the SDK.
    """
    api_key = os.environ.get("DEEPGRAM_API_KEY")
    if not api_key:
        print("Error: DEEPGRAM_API_KEY not found.")
        return b""

    url = "https://api.deepgram.com/v1/speak?model=aura-asteria-en&encoding=mp3"
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json"
    }
    payload = {"text": text}
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.content
    except Exception as e:
        print(f"TTS Error: {e}")
        return b""
