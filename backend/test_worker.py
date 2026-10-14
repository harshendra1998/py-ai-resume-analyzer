"""Run: docker compose run --rm worker python test_worker.py"""
import os
import tempfile

import docx
import fitz

from worker import extract_text, to_row

r = to_row({"name": " Jane Doe ", "email": "", "skills": "Python, SQL ,python", "experience_years": "4.5"})
assert r["name"] == "Jane Doe" and r["email"] is None and r["mobile"] is None, r
assert r["skills"] == ["Python", "SQL"], r["skills"]
assert r["experience_years"] == 4.5
assert to_row({"experience_years": "lots"})["experience_years"] is None

with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "cv.docx")
    doc = docx.Document()
    doc.add_paragraph("Jane Doe - jane@example.com")
    doc.save(p)
    assert "jane@example.com" in extract_text(p)

    p = os.path.join(d, "cv.pdf")
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "Jane Doe, Python developer, Pune, India. jane@example.com")
    pdf.save(p)
    assert "jane@example.com" in extract_text(p)

    p = os.path.join(d, "scan.pdf")  # too little text -> OCR path
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 120), "Jane Doe", fontsize=36)
    pdf.save(p)
    assert "Jane" in extract_text(p), extract_text(p)

print("ok")
