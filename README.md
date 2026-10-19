# CV Parser

Upload CVs (PDF / DOC / DOCX, including scanned PDFs) and get name, mobile, email, key skills,
location and details extracted into PostgreSQL by a local LLM (Ollama), then browse, analyse and rank them against a job description.

## Run

```bash
docker compose up -d        # first run downloads the LLM (~2 GB)
```

| What | URL | Login |
|---|---|---|
| App | http://localhost:5173 | `admin` / `admin123` (admin), `recruiter` / `recruiter123` (user) |
| API docs | http://localhost:8000/docs | |
| Keycloak console | http://localhost:8080 | `kcadmin` / `kcadmin` |

Settings go in `.env` (see `.env.example`):
- `LLM_MODEL`: Ollama model, default `qwen2.5:3b`. A larger model such as `qwen2.5:7b` extracts more accurately but is slower on CPU.
- `GROQ_API_KEY` (optional): used only when Ollama fails or returns invalid JSON. Those CVs/JDs are then sent to Groq's cloud API.

## Flow

```
browser ──login──▶ Keycloak (realm cvapp, roles admin/user)
   │ JWT
   ▼
React (Vite) ──/api──▶ FastAPI ──verifies JWT via Keycloak JWKS, checks role
                         ├─ admin: create/list users ──▶ Keycloak Admin API (service account cv-api)
                         ├─ user: upload 1..n CVs ──▶ save file, insert row (pending) ──▶ Redis queue
                         ├─ user: list CVs ◀── PostgreSQL
                         ├─ user: analysis ◀── Redis cache ◀── PostgreSQL aggregates
                         └─ user: match JD ──▶ LLM extracts requirements (cached in Redis) ──▶ score parsed CVs ──▶ top 5
Celery worker ◀── Redis queue
   text: PyMuPDF (PDF) · python-docx (DOCX) · antiword (DOC) · Tesseract OCR (scanned PDF)
   fields: Ollama (qwen2.5:3b, JSON mode; Groq fallback) ──▶ PostgreSQL row = done, cache invalidated
```

| Role | Can |
|---|---|
| admin | create users and assign a role, list users |
| user | upload one or many CVs, view the table (search, expand a row for details), view the analysis, match CVs to a JD |

## Layout

- `backend/app.py`: REST API (auth, users, uploads, list, analysis)
- `backend/worker.py`: Celery task (text extraction, OCR, LLM extraction) and JD matching
- `backend/db.py`: table definition, DB engine, Redis client
- `frontend/src/App.jsx`: UI
- `keycloak/cvapp-realm.json`: realm, clients, roles, seed users (imported on first start)

## JD matching

Paste a job description on the **Match JD** tab. The LLM pulls out the must-have skills, nice-to-have skills and minimum
experience. Every parsed CV is then scored out of 100: 60% for must-have skills, 10% for nice-to-have skills, 30% for experience
compared with the minimum asked. A skill counts if it is in the CV's extracted skills or appears in the CV text.
The top 5 distinct candidates with at least one must-have skill are shown, with matched and missing skills.

Check extraction, OCR and scoring: `docker compose run --rm worker python test_worker.py`

## Before production

Demo secrets are hard-coded (`cv-api-secret`, DB and Keycloak passwords) and Keycloak runs `start-dev`.
Move these into env/secrets, run Keycloak with `start` behind TLS, and serve a `vite build` instead of the dev server.
