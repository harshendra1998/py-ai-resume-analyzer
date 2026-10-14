import json
import os
import subprocess

import docx
import fitz  # PyMuPDF
import httpx
import pytesseract
from celery import Celery
from PIL import Image
from sqlalchemy import select, update

from db import ANALYSIS_KEY, cache, cvs, engine

celery = Celery("cv", broker=os.environ["REDIS_URL"])
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_MODEL = os.environ.get("GROQ_MODEL") or "openai/gpt-oss-20b"

PROMPT = """Extract candidate data from the resume below. Reply with JSON only, using exactly these keys:
{"name": string, "mobile": string, "email": string, "location": string (city, country),
 "skills": [string] (key technical and professional skills, at most 25),
 "experience_years": number (total professional experience in years),
 "current_title": string, "education": [string], "companies": [string],
 "summary": string (two sentences)}
Use null for anything not present in the resume. Do not invent data.

RESUME:
"""


def ocr_page(page):
    pix = page.get_pixmap(dpi=300)
    return pytesseract.image_to_string(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))


def extract_text(path):
    ext = path.rsplit(".", 1)[-1].lower()
    if ext == "pdf":
        with fitz.open(path) as doc:
            text = "\n".join(p.get_text() for p in doc)
            if len(text.strip()) < 50:  # no text layer -> scanned PDF, OCR it
                text = "\n".join(ocr_page(p) for p in doc)
        return text
    if ext == "docx":
        d = docx.Document(path)
        parts = [p.text for s in d.sections for p in s.header.paragraphs]  # contact info often lives in the header
        parts += [p.text for p in d.paragraphs]
        parts += [c.text for t in d.tables for r in t.rows for c in r.cells]
        return "\n".join(parts)
    if ext == "doc":
        return subprocess.run(["antiword", path], capture_output=True, text=True, check=True).stdout
    raise ValueError(f"unsupported file type: .{ext}")


def llm_extract(text):
    r = httpx.post("https://api.groq.com/openai/v1/chat/completions", timeout=120,
                   headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                   json={"model": GROQ_MODEL, "temperature": 0, "response_format": {"type": "json_object"},
                         "messages": [{"role": "user", "content": PROMPT + text[:12000]}]})  # ponytail: truncates very long CVs
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])


def to_row(d):
    """Normalise whatever the LLM returned into DB columns."""
    def s(k):
        v = d.get(k)
        return (str(v).strip() or None) if v is not None else None

    raw = d.get("skills") or []
    if isinstance(raw, str):
        raw = raw.split(",")
    seen, skills = set(), []
    for x in raw:
        x = str(x).strip()
        if x and x.lower() not in seen:
            seen.add(x.lower())
            skills.append(x)
    try:
        exp = float(d.get("experience_years"))
    except (TypeError, ValueError):
        exp = None
    return {
        "name": s("name"), "mobile": s("mobile"), "email": s("email"), "location": s("location"),
        "skills": skills[:25], "experience_years": exp,
        "details": {k: d.get(k) for k in ("current_title", "education", "companies", "summary")},
    }


@celery.task
def process_cv(cv_id):
    with engine.begin() as c:
        path = c.execute(select(cvs.c.path).where(cvs.c.id == cv_id)).scalar_one()
        c.execute(update(cvs).where(cvs.c.id == cv_id).values(status="processing"))
    try:
        text = extract_text(path).replace("\x00", "")
        if not text.strip():
            raise ValueError("no text found in document")
        values = {**to_row(llm_extract(text)), "raw_text": text, "status": "done", "error": None}
    except Exception as e:
        values = {"status": "failed", "error": str(e)[:500]}
    with engine.begin() as c:
        c.execute(update(cvs).where(cvs.c.id == cv_id).values(**values))
    cache.delete(ANALYSIS_KEY)
