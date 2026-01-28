# Bank ABC Voice Agent (POC)

This repo deploys as a single Vercel project (one domain):
- **Frontend**: React (Vite) built as static files.
- **Backend**: Python (FastAPI) serverless functions.

## 🚀 Live Demo
- **Frontend App**: [https://voice-agent-for-bank.vercel.app](https://voice-agent-for-bank.vercel.app)
- **API Base URL**: `https://voice-agent-for-bank.vercel.app/api`

## 🔑 Test Credentials
Use these credentials to verify identity and access protected flows (e.g., "Check Balance", "Block Card"):
- **Customer ID**: `user123`
- **PIN**: `1234`

## 🛠 Setup Instructions

### Prerequisites
- Python 3.9+
- Node.js 18+
- API Keys: Groq, Deepgram, LangChain (optional)

### 1. Backend (FastAPI)
1. Navigate to `backend/`:
   ```bash
   cd backend
   ```
2. Create virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate  # or venv\Scripts\activate on Windows
   pip install -r requirements.txt
   ```
3. Create `.env` file:
   ```bash
   cp .env.example .env
   ```
   Add your keys: `GROQ_API_KEY`, `DEEPGRAM_API_KEY`.
4. Run locally:
   ```bash
   python main.py
   # Runs on http://localhost:8000
   ```

### 2. Frontend (React + Vite)
1. Navigate to `frontend/`:
   ```bash
   cd frontend
   ```
2. Install dependencies:
   ```bash
   npm install
   ```
3. Configure environment:
   Create `.env` with:
   ```
   VITE_API_URL=http://localhost:8000
   ```
4. Run locally:
   ```bash
   npm run dev
   # Runs on http://localhost:5173
   ```

## 🎮 How to Demo (End-to-End)
1. Open the **Frontend App** (Local or Live).
2. Click the **Start Call** button (allow microphone access).
3. **Scenario 1: General Inquiry**
   - Say: *"What are your operating hours?"*
   - Expected: Agent answers without asking for ID.
4. **Scenario 2: Secure Banking (Happy Path)**
   - Say: *"What is my checking account balance?"*
   - Agent: *"I can help with that. What is your Customer ID?"*
   - Say: *"user123"*
   - Agent: *"Please share your PIN to verify your identity."*
   - Say: *"1234"* (or *"one two three four"*)
   - Agent: *"Thank you. Your checking account balance is $5,000.00."*
5. **Scenario 3: Guardrails**
   - Say: *"Block my card."*
   - Agent should ask for verification first (if not already verified) and confirm the reason.

## 🛡️ Resiliency & Rate Limiting

To handle Groq's free tier rate limits (429 Errors), the backend implements an **Automatic Model Switching** mechanism:

1.  **Primary Model**: `llama-3.3-70b-versatile` (Best performance).
2.  **Fallbacks**: If the primary model is rate-limited, the system automatically retries with:
    - `llama-3.1-8b-instant` (Fastest, lower reasoning).
    - `llama-3.1-70b-versatile`
    - `gemma2-9b-it`
    - `mixtral-8x7b-32768`
3.  **Configurable**: You can customize the fallback order via the `GROQ_MODEL_FALLBACKS` environment variable.

## ⚖️ Trade-offs & Assumptions

### Architecture Trade-offs
1.  **Latency vs. Simplicity**:
    *   *Current*: **Turn-based HTTP** (Audio Upload -> Transcribe -> LLM -> TTS -> Audio Download).
    *   *Trade-off*: Simpler to implement and debug, but higher latency (2-4s) compared to full-duplex WebSocket streaming.
2.  **State Management**:
    *   *Current*: **In-Memory / Optional DB**.
    *   *Trade-off*: In-memory is fast for demos but state is lost on serverless cold starts. Supabase integration is added for persistence but adds a network hop.
3.  **Voice Activity Detection (VAD)**:
    *   *Current*: **Client-side VAD** (ONNX).
    *   *Trade-off*: Reduces server load and bandwidth, but relies on client device performance.

### Assumptions
- **Language**: English only.
- **Security**:
    - "Authentication" is a mock check against a static dictionary. **NOT** for production use.
    - PINs are transmitted as part of the conversation transcript (in a real app, this would be out-of-band or DTMF).
- **Environment**:
    - Browser must support MediaRecorder API.
    - Rate limits apply to Groq and Deepgram free tiers.

## 📝 Environment Notes
- **Rate Limits**: The demo uses free/tier-limited keys for Groq and Deepgram. Excessive usage may result in 429 errors.
- **Cold Starts**: On Vercel, the first request might take a few extra seconds to spin up the Python function.
