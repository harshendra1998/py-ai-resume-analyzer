# CV Parser

Upload CVs (PDF / DOC / DOCX, including scanned PDFs) and get name, mobile, email, key skills,
location and details extracted into PostgreSQL by an LLM (Groq), then browse and analyse them.

## Run

```bash
echo GROQ_API_KEY=gsk_... > .env   # get one at console.groq.com
docker compose up -d
```

| What | URL | Login |
|---|---|---|
| App | http://localhost:5173 | `admin` / `admin123` (admin), `recruiter` / `recruiter123` (user) |
| API docs | http://localhost:8000/docs | |
| Keycloak console | http://localhost:8080 | `kcadmin` / `kcadmin` |

Optional: `GROQ_MODEL=...` in `.env` (default `openai/gpt-oss-20b`). CV text is sent to Groq's API for extraction.

## Flow

```
browser ──login──▶ Keycloak (realm cvapp, roles admin/user)
   │ JWT
   ▼
React (Vite) ──/api──▶ FastAPI ──verifies JWT via Keycloak JWKS, checks role
                         ├─ admin: create/list users ──▶ Keycloak Admin API (service account cv-api)
                         ├─ user: upload 1..n CVs ──▶ save file, insert row (pending) ──▶ Redis queue
                         ├─ user: list CVs ◀── PostgreSQL
                         └─ user: analysis ◀── Redis cache ◀── PostgreSQL aggregates
Celery worker ◀── Redis queue
   text: PyMuPDF (PDF) · python-docx (DOCX) · antiword (DOC) · Tesseract OCR (scanned PDF)
   fields: Groq API (gpt-oss-20b, JSON mode) ──▶ PostgreSQL row = done, cache invalidated
```

| Role | Can |
|---|---|
| admin | create users and assign a role, list users |
| user | upload one or many CVs, view the table (search, expand a row for details), view the analysis |

## Layout

- `backend/app.py`: REST API (auth, users, uploads, list, analysis)
- `backend/worker.py`: Celery task (text extraction, OCR, LLM extraction)
- `backend/db.py`: table definition, DB engine, Redis client
- `frontend/src/App.jsx`: UI
- `keycloak/cvapp-realm.json`: realm, clients, roles, seed users (imported on first start)

Check extraction and OCR: `docker compose run --rm worker python test_worker.py`

## Before production

Demo secrets are hard-coded (`cv-api-secret`, DB and Keycloak passwords) and Keycloak runs `start-dev`.
Move these into env/secrets, run Keycloak with `start` behind TLS, and serve a `vite build` instead of the dev server.
