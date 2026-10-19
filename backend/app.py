import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Literal

import httpx
import jwt
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import insert, select, text

from db import ANALYSIS_KEY, cache, cvs, engine, meta
from worker import jd_requirements, process_cv, top_matches

KC_URL = os.environ["KEYCLOAK_URL"]  # internal URL, e.g. http://keycloak:8080
REALM = "cvapp"
ISSUER = os.environ["KEYCLOAK_ISSUER"]  # URL the browser logs in through
WEB_CLIENT = "cv-web"
UPLOAD_DIR = Path(os.environ["UPLOAD_DIR"])
ALLOWED = {".pdf", ".docx", ".doc"}
MAX_BYTES = 10 * 1024 * 1024

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
meta.create_all(engine)  # ponytail: no migrations tool; add Alembic once the schema starts changing
jwks = jwt.PyJWKClient(f"{KC_URL}/realms/{REALM}/protocol/openid-connect/certs")
app = FastAPI(title="CV Parser API")


# ---------- auth ----------

def current_user(cred: HTTPAuthorizationCredentials = Depends(HTTPBearer())):
    try:
        key = jwks.get_signing_key_from_jwt(cred.credentials).key
        claims = jwt.decode(cred.credentials, key, algorithms=["RS256"], issuer=ISSUER,
                            options={"verify_aud": False})  # Keycloak sets aud=account; azp is checked below
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"Invalid token: {e}")
    if claims.get("azp") != WEB_CLIENT:
        raise HTTPException(401, "Token was not issued to this app")
    claims["roles"] = claims.get("realm_access", {}).get("roles", [])
    return claims


def require(role):
    def dep(user=Depends(current_user)):
        if role not in user["roles"]:
            raise HTTPException(403, f"Requires the '{role}' role")
        return user
    return dep


@app.get("/api/me")
def me(user=Depends(current_user)):
    return {"username": user.get("preferred_username"), "roles": user["roles"]}


# ---------- admin: users live in Keycloak ----------

def keycloak_admin():
    r = httpx.post(f"{KC_URL}/realms/{REALM}/protocol/openid-connect/token", data={
        "grant_type": "client_credentials", "client_id": "cv-api",
        "client_secret": os.environ["KEYCLOAK_API_SECRET"]})
    r.raise_for_status()
    return httpx.Client(base_url=f"{KC_URL}/admin/realms/{REALM}",
                        headers={"Authorization": f"Bearer {r.json()['access_token']}"})


def kc_check(r):
    if r.is_success:
        return r
    try:
        msg = r.json().get("errorMessage") or r.text
    except ValueError:
        msg = r.text
    raise HTTPException(r.status_code if r.status_code in (400, 409) else 502, msg)


class NewUser(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9._-]+$")
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    password: str = Field(min_length=8)
    role: Literal["user", "admin"] = "user"


@app.post("/api/admin/users", status_code=201)
def create_user(body: NewUser, _=Depends(require("admin"))):
    with keycloak_admin() as kc:
        r = kc_check(kc.post("/users", json={
            "username": body.username, "email": body.email, "firstName": body.first_name,
            "lastName": body.last_name, "enabled": True, "emailVerified": True,
            "credentials": [{"type": "password", "value": body.password, "temporary": False}]}))
        uid = r.headers["Location"].rsplit("/", 1)[-1]
        role = kc_check(kc.get(f"/roles/{body.role}")).json()
        kc_check(kc.post(f"/users/{uid}/role-mappings/realm", json=[role]))
    return {"id": uid, "username": body.username, "role": body.role}


@app.get("/api/admin/users")
def list_users(_=Depends(require("admin"))):
    with keycloak_admin() as kc:
        users = kc_check(kc.get("/users", params={"max": 500})).json()
        members = {role: {u["id"] for u in kc_check(kc.get(f"/roles/{role}/users", params={"max": 500})).json()}
                   for role in ("admin", "user")}
    return [{"id": u["id"], "username": u["username"], "email": u.get("email"),
             "name": f"{u.get('firstName', '')} {u.get('lastName', '')}".strip(),
             "roles": [r for r, ids in members.items() if u["id"] in ids],
             "created": u.get("createdTimestamp")}
            for u in users if not u["username"].startswith("service-account-")]


# ---------- CVs ----------

@app.post("/api/cvs", status_code=202)
def upload_cvs(files: list[UploadFile] = File(...), user=Depends(require("user"))):
    blobs = []
    for f in files:  # validate everything before saving anything
        ext = Path(f.filename or "").suffix.lower()
        if ext not in ALLOWED:
            raise HTTPException(400, f"{f.filename}: only PDF, DOC and DOCX files are accepted")
        data = f.file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise HTTPException(413, f"{f.filename}: larger than 10 MB")
        blobs.append((f.filename, ext, data))

    queued = []
    for filename, ext, data in blobs:
        path = UPLOAD_DIR / f"{uuid.uuid4().hex}{ext}"
        path.write_bytes(data)
        with engine.begin() as c:
            cv_id = c.execute(insert(cvs).values(filename=filename, path=str(path),
                                                 uploaded_by=user["preferred_username"]).returning(cvs.c.id)).scalar_one()
        process_cv.delay(cv_id)
        queued.append({"id": cv_id, "filename": filename})
    cache.delete(ANALYSIS_KEY)
    return queued


LIST_COLS = [c for c in cvs.c if c.name not in ("raw_text", "path")]


@app.get("/api/cvs")
def list_cvs(_=Depends(require("user"))):
    # ponytail: returns every CV, the UI filters; add server-side paging past a few thousand rows
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(select(*LIST_COLS).order_by(cvs.c.id.desc()))]


@app.get("/api/analysis")
def analysis(_=Depends(require("user"))):
    if hit := cache.get(ANALYSIS_KEY):
        return json.loads(hit)
    with engine.connect() as c:
        def q(sql):
            return [dict(r._mapping) for r in c.execute(text(sql))]
        out = {
            "summary": q("""select count(*) as total, count(*) filter (where status = 'done') as parsed,
                              round(avg(experience_years)::numeric, 1)::float as avg_experience,
                              count(distinct lower(location)) as locations from cvs""")[0],
            "status": q("select status, count(*) as count from cvs group by 1 order by 2 desc"),
            "top_skills": q("""select mode() within group (order by s) as skill, count(*) as count
                               from cvs, unnest(skills) s where status = 'done'
                               group by lower(s) order by 2 desc, 1 limit 15"""),
            "locations": q("""select coalesce(location, 'Unknown') as location, count(*) as count from cvs
                              where status = 'done' group by 1 order by 2 desc limit 10"""),
            "experience": q("""select case when experience_years is null then 'Unknown'
                                           when experience_years < 2 then '0-2 yrs'
                                           when experience_years < 5 then '2-5 yrs'
                                           when experience_years < 10 then '5-10 yrs'
                                           else '10+ yrs' end as bucket, count(*) as count
                               from cvs where status = 'done' group by 1
                               order by min(experience_years) nulls last"""),
        }
    cache.set(ANALYSIS_KEY, json.dumps(out), ex=300)
    return out


class MatchRequest(BaseModel):
    jd: str = Field(min_length=30, max_length=20000)


@app.post("/api/match")
def match(body: MatchRequest, _=Depends(require("user"))):
    key = "jd:" + hashlib.sha256(body.jd.strip().encode()).hexdigest()  # same JD -> skip the LLM call
    if hit := cache.get(key):
        req = json.loads(hit)
    else:
        try:
            req = jd_requirements(body.jd)
        except Exception as e:
            raise HTTPException(502, f"Could not read the job description with the LLM: {e}")
        cache.set(key, json.dumps(req), ex=86400)
    # ponytail: scores every parsed CV in Python; move the skill filter into SQL past ~10k CVs
    with engine.connect() as c:
        rows = c.execute(select(*LIST_COLS, cvs.c.raw_text).where(cvs.c.status == "done")).mappings().all()
    return {"requirements": req, "candidates": top_matches(req, rows)}
