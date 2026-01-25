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
   - MongoDB:
     - `MONGODB_URI`
     - `MONGODB_DB_NAME`
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

## MongoDB env variables

Add these to `backend/.env` locally, and to Vercel Environment Variables when deploying:
- `MONGODB_URI` (Atlas SRV format): `mongodb+srv://<user>:<password>@<cluster-host>/<db>?retryWrites=true&w=majority`
- `MONGODB_DB_NAME` (optional): `bank_abc_voice_agent`

## Admin dashboard

Use the **Admin** button in the main UI to edit:
- Prompts (system + router)
- Tool enable/disable flags
- Routing rules JSON

## Notes

- The call flow asks for Customer ID + PIN before starting the call. PIN is never stored.
