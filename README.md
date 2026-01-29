# Bank ABC Voice Agent (POC)

This repository contains the source code for the "Bank ABC" Voice AI Proof of Concept. It demonstrates a conversational banking agent capable of verifying identity, checking balances, and handling card security flows using voice interaction.

## 🚀 Live Demos

| Version | Description | URL | Frontend Host | Backend Host |
| :--- | :--- | :--- | :--- | :--- |
| **v3 (Recommended)** | **Real-time WebSocket** streaming (Low Latency) | [Live App](https://voice-agent-for-bank-with-websocket.vercel.app) | **Vercel** | **Render** (Docker) |
| **v1 (Baseline)** | Turn-based HTTP (Higher Latency) | [Live App](https://voice-agent-for-bank.vercel.app) | **Vercel** | **Vercel** (Serverless) |

## 🔑 Test Credentials
Use these credentials to verify identity and access protected flows (e.g., "Check Balance", "Block Card"):
- **Customer ID**: `John123`
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
   - Say: *"John123"*
   - Agent: *"Please share your PIN to verify your identity."*
   - Say: *"1234"* (or *"one two three four"*)
   - Agent: *"Thank you. Your checking account balance is $5,000.00."*
5. **Scenario 3: Guardrails**
   - Say: *"Block my card."*
   - Agent should ask for verification first (if not already verified) and confirm the reason.


## Demo & Trace Evidence: "Balance Check" scenario

https://smith.langchain.com/public/0d75eabc-e192-42d0-87d0-7e537d9f281c/r (https://smith.langchain.com/public/0d75eabc-e192-42d0-87d0-7e537d9f281c/r)

## 🌐 Deployment Strategy: Vercel vs. Render (WebSocket)

### 1. The Constraints
The initial requirements suggested deploying the full stack on Vercel. However, **Vercel Serverless Functions do not support long-lived WebSocket connections**. They are designed for short-lived HTTP requests (stateless), making real-time, full-duplex voice streaming impossible in a pure Vercel environment.

### 2. The Solution (Hybrid Architecture)
To support low-latency voice interactions, I created a separate branch (`v3-with-websocket`) with a hybrid deployment model:

*   **Frontend**: Hosted on **Vercel** (Static React App).
*   **Backend**: Hosted on **Render** (Docker Container) to support persistent WebSocket connections.

| Feature | Non-WebSocket (HTTP) | WebSocket (Real-Time) |
| :--- | :--- | :--- |
| **URL** | [https://voice-agent-for-bank.vercel.app](https://voice-agent-for-bank.vercel.app) | [https://voice-agent-for-bank-with-websocket.vercel.app](https://voice-agent-for-bank-with-websocket.vercel.app) |
| **Backend Host** | Vercel (Serverless) | Render (Docker) |
| **Latency** | ~2-4s (Turn-based) | ~500ms-1s (Streaming) |
| **Protocol** | HTTP POST | WSS (Secure WebSocket) |

---

## 🚧 Scope & Limitations (POC Status)

This project is a **Proof of Concept (POC)** designed to demonstrate core capabilities. While it implements the foundational logic for banking operations, it is not a production-ready system.

### Supported Flows (Mocked)
The agent is trained to handle the following scenarios using mock data:
1.  **Card & ATM Issues**: Blocking cards, reporting lost items.
2.  **Account Servicing**: Checking balances, updating address/email.
3.  **Account Opening**: Basic eligibility Q&A.
4.  **Transactions**: Listing recent transactions.

**Note**: "Digital App Support", "Transfers & Bill Payments", and "Account Closure" flows are currently handled via **LLM Conversational Fallbacks**. The agent will provide general guidance or offer to transfer the call, as no specific backend tools (APIs) have been implemented for these flows yet.

### Known Limitations
*   **Edge Cases**: Complex, multi-turn interruptions or ambiguous intents might confuse the agent.
*   **Prompt Tuning**: As a POC, the system prompts are optimized for happy paths. Adversarial testing (trying to break the agent) may succeed.

---

## 🔒 Security & Verification Notes

### 1. PIN vs. Knowledge-Based Authentication (KBA)
*   **Real World**: Banks typically use KBA (e.g., "What was your last transaction amount?") or out-of-band 2FA (SMS/App push) for verification.
*   **This POC**: Implements a **PIN-based verification** (Customer ID + 4-digit PIN) as explicitly requested in the assessment guidelines.
*   **Warning**: In this demo, the PIN is spoken and transcribed. In a production voice app, this would be handled via **DTMF** (keypad entry) to avoid exposing secrets in logs.

### 2. Admin Interface & Database
*   **Supabase Integration**: The project is connected to a Supabase PostgreSQL database to meet the requirement for a "quick-to-iterate micro app platform."
*   **Capabilities**:
    *   **Call Logs**: All conversations and tool outputs are stored in `call_turns`.
    *   **Configuration**: System prompts and routing rules are stored in `agent_configs`.
*   **Admin Dashboard**: The frontend includes an "Admin" button to view/edit these configurations in real-time.
*   **Security Warning**: The Admin interface currently **does not have authentication**. It is open for demonstration purposes. In production, this would be behind a secure login (e.g., Supabase Auth).

---

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
    *   **Turn-based HTTP (V1)**: Simpler to implement but suffers from 2-4s latency.
    *   **WebSocket Streaming (V3)**: Requires a long-running server (Render/Docker) but delivers sub-second interaction.
2.  **State Management**:
    *   **In-Memory**: Fast for demos but state is lost on serverless cold starts.
    *   **Supabase (PostgreSQL)**: Added for persistence and to support the "quick-to-iterate" configuration requirement, though it adds a network hop.
3.  **Voice Activity Detection (VAD)**:
    *   **Client-side**: Reduces server load/bandwidth but relies on the user's device performance.

### Assumptions
- **Language**: English only.
- **Environment**: Browser must support `MediaRecorder` API.
- **Rate Limits**: The demo runs on free-tier keys (Groq/Deepgram), so aggressive testing may hit limits (handled by model switching).

## 📝 Environment Notes
- **Rate Limits**: The demo uses free/tier-limited keys for Groq and Deepgram. Excessive usage may result in 429 errors.
- **Cold Starts**: On Vercel, the first request might take a few extra seconds to spin up the Python function.
