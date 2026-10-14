# CV Parser: Project Overview

## 1. What the project does

CV Parser is a web app for recruiters. You upload CVs in PDF, DOC or DOCX format, including scanned PDFs. The app reads each file and pulls out structured data: name, mobile, email, location, key skills, years of experience, current title, education, companies and a short summary. It stores the results in PostgreSQL. You can then search the CVs in a table and see aggregate analysis such as top skills, locations and experience spread.

There are two roles:

| Role | Can do |
|---|---|
| **admin** | Create users, assign them a role, list all users |
| **user** (recruiter) | Upload one or many CVs, browse and search the parsed table, open the details of a row, view the Analysis dashboard |

---

## 2. High-level architecture

The whole system runs with `docker compose up` as **6 containers**:

```
                ┌──────────────────────┐
   Browser ───▶ │ web  (React + Vite)  │  :5173
                └─────────┬────────────┘
         login (OIDC)     │  /api/*  (proxied, with JWT)
      ┌───────────────────┘          │
      ▼                              ▼
┌────────────┐  JWKS / Admin API ┌───────────────┐   enqueue job   ┌─────────┐
│ keycloak   │ ◀──────────────── │ api (FastAPI) │ ──────────────▶ │  redis  │
│ :8080      │                   │ :8000         │ ◀── cache ───── │         │
└─────┬──────┘                   └──────┬────────┘                 └────┬────┘
      │                                 │ SQL                           │ job
      │                                 ▼                               ▼
      │                          ┌─────────────┐   update row    ┌──────────────┐
      └────── own DB ──────────▶ │  postgres   │ ◀────────────── │ worker       │
                                 │  (cv, kc)   │                 │ (Celery)     │
                                 └─────────────┘                 └──────┬───────┘
                                                                        │ HTTPS
                                                                        ▼
                                                                  Groq LLM API
```

| Container | Image / build | Role |
|---|---|---|
| `postgres` | `postgres:16-alpine` | Stores CV data (`cv` DB) and Keycloak's data (`keycloak` DB) |
| `redis` | `redis:7-alpine` | Celery job queue (broker) and cache for the analysis endpoint |
| `keycloak` | `keycloak:26.0` | Login, users, roles, JWT issuing |
| `api` | `./backend` | FastAPI REST API |
| `worker` | `./backend` (same image as `api`) | Celery worker that parses CVs in the background |
| `web` | `node:22-alpine` | Vite dev server serving the React app and proxying `/api` to `api` |

### Why it is split this way
- **Parsing is slow.** OCR and the LLM call can take seconds per CV. The API answers at once with `202 Accepted` and hands the work to a background worker through Redis, so uploading many CVs never blocks the request.
- **Keycloak handles authentication.** The app keeps no password table. Keycloak handles login, password storage, tokens and roles. The API only verifies the signed JWT.
- **One backend image, two processes.** `api` and `worker` share the same code (`db.py` and `worker.py`) and differ only in the start command.

---

## 3. End-to-end flow

### Login
1. The browser opens the app. `keycloak-js` redirects to the Keycloak login page (OIDC + PKCE).
2. After login, Keycloak returns a **JWT access token** that lists the user's realm roles (`admin` / `user`).
3. The React app attaches `Authorization: Bearer <token>` to every `/api` call and refreshes the token automatically.

### Uploading a CV
1. The UI sends the files as `multipart/form-data` to `POST /api/cvs`.
2. The API checks the JWT and the `user` role. It then validates **all** files (extension and a 10 MB limit) before saving any of them.
3. Each file is saved to the shared `uploads` volume under a random UUID name. The API inserts a row in `cvs` with `status = pending` and calls `process_cv.delay(id)` to push a job onto Redis.
4. The UI polls `GET /api/cvs` every 3 s while any row is `pending` or `processing`.

### Background parsing (worker)
1. The worker marks the row `processing`.
2. **Text extraction** depends on the file type:
   - **PDF**: PyMuPDF reads the text layer. If it finds fewer than 50 characters, the PDF is treated as **scanned**: each page is rendered at 300 DPI and run through **Tesseract OCR**.
   - **DOCX**: python-docx reads header paragraphs (contact details often live there), body paragraphs and tables.
   - **DOC** (legacy Word): the `antiword` command-line tool.
3. **Field extraction**: the text (first 12,000 characters) goes to the **Groq LLM API** (`openai/gpt-oss-20b` by default) in JSON mode with temperature 0 and a fixed prompt that lists the exact keys to return.
4. **Normalisation** (`to_row`): trims strings, turns empty values into `null`, de-duplicates skills case-insensitively (max 25) and parses experience as a number.
5. The row is updated to `done`, or to `failed` with the error message. The analysis cache in Redis is cleared.

### Analysis
`GET /api/analysis` runs a few SQL aggregate queries: totals, status counts, top 15 skills, top 10 locations and experience buckets. It caches the result in Redis for 5 minutes. Any upload or finished parse clears the cache.

---

## 4. Folder structure

```
Ai Resume review/
├── docker-compose.yml        # All 6 services, env vars, volumes, health checks
├── .env.example              # GROQ_API_KEY (required), GROQ_MODEL (optional)
├── README.md                 # Quick start
│
├── backend/                  # Python: API + worker (one Docker image)
│   ├── Dockerfile            # python:3.12-slim + tesseract-ocr + antiword
│   ├── requirements.txt      # Python dependencies
│   ├── db.py                 # DB engine, Redis client, `cvs` table definition
│   ├── app.py                # FastAPI app: auth, admin/users, CV upload/list, analysis
│   ├── worker.py             # Celery task: text extraction, OCR, LLM call, normalisation
│   └── test_worker.py        # Self-check for extraction, OCR and normalisation
│
├── frontend/                 # React single-page app
│   ├── index.html            # HTML shell
│   ├── package.json          # JS dependencies
│   ├── vite.config.js        # Dev server + /api proxy to the backend
│   └── src/
│       ├── main.jsx          # Keycloak init, `api()` fetch wrapper, renders <App>
│       ├── App.jsx           # Whole UI: tabs, CV table, details, analysis, users
│       └── style.css         # Styling (plain CSS)
│
├── keycloak/
│   └── cvapp-realm.json      # Realm "cvapp": clients, roles, seed users (auto-imported)
│
└── postgres/
    └── init.sql              # Creates the extra `keycloak` database on first start
```

### Key files in more detail

**`backend/db.py`** holds the shared setup for API and worker: a SQLAlchemy engine, a Redis client and the `cvs` table definition (SQLAlchemy Core, not the ORM).

**`backend/app.py`** is the REST API.
- `current_user`: verifies the JWT signature against Keycloak's public keys (JWKS), checks the issuer and that the token was issued to the `cv-web` client, and reads the roles.
- `require(role)`: a FastAPI dependency that returns 403 if the role is missing.
- Admin endpoints call the **Keycloak Admin REST API** with a service account (`cv-api`, client-credentials grant).
- CV endpoints cover upload, list and analysis.
- `meta.create_all(engine)` creates the table at startup (there is no migration tool yet).

**`backend/worker.py`** contains the Celery app and the `process_cv` task, plus the `extract_text`, `ocr_page`, `llm_extract` and `to_row` helpers.

**`frontend/src/main.jsx`** initialises Keycloak (`login-required`, PKCE S256) and defines `api(path, opts)`, a `fetch` wrapper that refreshes the token, adds the Bearer header and turns FastAPI error responses into readable messages.

**`frontend/src/App.jsx`** builds the whole UI from small components:
- `App`: the header and role-based tabs (users see *CVs* and *Analysis*, admins see *Users*)
- `Cvs`: upload buttons (single and multiple files), client-side search, the table with expandable rows, and polling
- `Details`: the expanded row with current role, summary, education, companies and all skills
- `Analysis` / `Tile` / `Bars`: summary tiles and pure-CSS bar charts (no chart library)
- `Users`: the create-user form and the users table

**`keycloak/cvapp-realm.json`** defines everything Keycloak needs, imported on first start:
- Clients: `cv-web` (public client for the browser) and `cv-api` (confidential client with a service account for the Admin API)
- Realm roles: `admin` and `user`
- Seed users: `admin` / `admin123` and `recruiter` / `recruiter123`

---

## 5. Data model

There is one table, `cvs`:

| Column | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `filename` | text | Original file name |
| `path` | text | Where the file sits on the uploads volume |
| `status` | text | `pending` → `processing` → `done` / `failed` |
| `error` | text | Failure reason, if any |
| `name`, `mobile`, `email`, `location` | text | Extracted by the LLM |
| `skills` | text[] | PostgreSQL array, de-duplicated, max 25 |
| `experience_years` | float | |
| `details` | JSONB | `current_title`, `education`, `companies`, `summary` |
| `raw_text` | text | Full extracted text (not sent to the UI) |
| `uploaded_by` | text | Keycloak username |
| `created_at` | timestamptz | |

Users are **not** stored in this database. They live in Keycloak.

---

## 6. API endpoints

Interactive docs are at http://localhost:8000/docs (Swagger, generated by FastAPI).

| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/api/me` | any logged-in user | Returns the username and roles |
| POST | `/api/admin/users` | admin | Creates a Keycloak user and assigns a role |
| GET | `/api/admin/users` | admin | Lists users with their roles |
| POST | `/api/cvs` | user | Uploads 1..n CVs and queues them (202) |
| GET | `/api/cvs` | user | Lists all CVs, newest first |
| GET | `/api/analysis` | user | Returns aggregated stats (cached for 5 min) |

---

## 7. Libraries and tools used

### Backend (Python 3.12)

| Library | Used for |
|---|---|
| **FastAPI** | REST framework: routing, request validation, dependency injection for auth, auto-generated Swagger docs |
| **Uvicorn** | ASGI server that runs FastAPI |
| **Pydantic** (comes with FastAPI) | Validates the create-user request body (username pattern, email, password length, role) |
| **python-multipart** | Lets FastAPI accept file uploads (`multipart/form-data`) |
| **SQLAlchemy 2.0** (Core) | Table definition and SQL query building |
| **psycopg 3** | PostgreSQL driver used by SQLAlchemy |
| **Celery** | Background job queue: runs `process_cv` in the worker container |
| **redis-py** | Redis client for the analysis cache (Celery also uses Redis as its broker) |
| **PyJWT [crypto]** | Verifies Keycloak's RS256-signed JWTs, fetching public keys from the JWKS endpoint |
| **httpx** | HTTP client for the Keycloak Admin API and the Groq LLM API |
| **PyMuPDF** (`fitz`) | Reads PDF text and renders pages to images for OCR |
| **python-docx** | Reads DOCX files (headers, paragraphs, tables) |
| **pytesseract** (+ Pillow) | Python wrapper around Tesseract OCR for scanned PDFs |

System packages installed in the Docker image:

| Tool | Used for |
|---|---|
| **tesseract-ocr** | OCR engine that turns page images into text |
| **antiword** | Converts legacy `.doc` (Word 97-2003) files to text |

### Frontend

| Library | Used for |
|---|---|
| **React 18** | UI components and state (hooks only, no state library) |
| **react-dom** | Renders React into the page |
| **keycloak-js** | OIDC login, token storage and token refresh in the browser |
| **Vite 5** | Dev server, hot reload, build tool, and the `/api` proxy to the backend |
| **@vitejs/plugin-react** | JSX support and fast refresh for Vite |

The frontend deliberately uses no router, no UI kit and no chart library. Tabs are plain state, styling is plain CSS and the bar charts are CSS widths.

### Infrastructure and services

| Service | Used for |
|---|---|
| **Docker Compose** | Runs the whole stack with one command |
| **PostgreSQL 16** | Main database (also hosts Keycloak's database) |
| **Redis 7** | Job queue broker and cache |
| **Keycloak 26** | Identity provider: login, users, roles, JWTs, Admin API |
| **Groq API** | Hosted LLM (default model `openai/gpt-oss-20b`) that turns raw CV text into JSON fields |

---

## 8. Security model

- **Authentication**: Keycloak OIDC with PKCE. The browser never sees a client secret.
- **Token verification**: the API checks the JWT signature (RS256, keys from JWKS), the issuer and `azp == cv-web`.
- **Authorization**: role checks on every endpoint through `require("admin")` / `require("user")`. The UI also hides tabs by role, but the API is what enforces access.
- **Uploads**: only `.pdf`, `.doc` and `.docx` are accepted, each at most 10 MB. Files are stored under random UUID names, so a user-supplied file name never becomes a path.
- **Admin actions**: the API calls Keycloak with its own service account (`cv-api`), not the user's token.
- **Privacy note**: CV text is sent to Groq's external API for extraction.

---

## 9. How to run

```bash
echo GROQ_API_KEY=gsk_... > .env     # key from console.groq.com
docker compose up -d
```

| What | URL | Login |
|---|---|---|
| App | http://localhost:5173 | `admin` / `admin123`, `recruiter` / `recruiter123` |
| API docs | http://localhost:8000/docs | |
| Keycloak console | http://localhost:8080 | `kcadmin` / `kcadmin` |

Run the extraction and OCR self-check:
```bash
docker compose run --rm worker python test_worker.py
```

---

## 10. Known limitations and next steps

| Current choice | Limit | Upgrade path |
|---|---|---|
| `create_all` at startup | No schema migrations | Add Alembic when the schema changes |
| `GET /api/cvs` returns every row and the UI filters | Slows down past a few thousand CVs | Server-side search and pagination |
| Only the first 12,000 characters go to the LLM | Very long CVs get cut off | Chunk the text or use a larger-context model |
| UI polls every 3 s | Extra requests while parsing | WebSockets or Server-Sent Events |
| Demo secrets in `docker-compose.yml`, Keycloak `start-dev`, Vite dev server | Not production-ready | Move secrets to env/secret store, run Keycloak with `start` behind TLS, serve the `vite build` output |
