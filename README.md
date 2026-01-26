# Bank ABC Voice Agent (POC)

This repo deploys as a single Vercel project (one domain):
- Frontend is built as static files into `public/`
- Backend is a FastAPI app exported from `index.py`

## Local setup

### 1) Backend (FastAPI)

1. Create your local env file:
   - Copy `backend/.env.example` → `backend/.env`
2. Fill in required keys in `backend/.env`:
   - `GROQ_API_KEY`
   - `DEEPGRAM_API_KEY`
   - `LANGCHAIN_API_KEY` (optional but recommended for tracing)
   - Supabase:
     - `SUPABASE_URL`
     - `SUPABASE_SERVICE_KEY`
3. Install Python deps:
   - `python -m pip install -r backend/requirements.txt`
4. Run the API:
   - `python -m uvicorn main:app --host 0.0.0.0 --port 8000`

### 2) Frontend (Vite)

1. Install deps:
   - `cd frontend`
   - `npm ci`
2. Configure API URL for local dev:
   - `frontend/.env` should contain: `VITE_API_URL=http://localhost:8000`
3. Run dev server:
   - `npm run dev`

Open the UI from the Vite URL (usually `http://localhost:5173`).

## Supabase Database Setup

1. Create a new Supabase project.
2. Run the SQL migrations in `supabase/migrations/` to set up the `call_sessions` and `call_turns` tables.
3. Get your Project URL and Service Role Key (Settings -> API) and add them to your `.env` file.

## Test Credentials

Use these credentials to test the "Deep Logic" flows (e.g., "Check Balance", "Block Card"):

- **Customer ID**: `user123`
- **PIN**: `1234`

## Admin dashboard

Use the **Admin** button in the main UI to edit:
- Prompts (system + router)
- Tool enable/disable flags
- Routing rules JSON

## Notes

- The call flow asks for Customer ID + PIN before starting the call. PIN is never stored.
