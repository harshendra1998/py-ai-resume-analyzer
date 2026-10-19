"""Run: docker compose run --rm worker python test_worker.py"""
import os
import tempfile

import docx
import fitz

from worker import extract_text, norm, to_row, top_matches

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

# JD matching
assert norm("React.js") == norm("react") and norm("Node JS") == "node" and norm("C++") == "c++"
req = {"required_skills": ["Python", "React", "Java"], "nice_to_have_skills": ["Docker"], "min_experience_years": 4}


def cv(id, name, skills, exp, text=""):
    return {"id": id, "name": name, "email": f"{name}@x.com", "mobile": None, "location": None, "filename": "f",
            "details": {}, "skills": skills, "experience_years": exp, "raw_text": text}


ranked = top_matches(req, [
    cv(1, "ana", ["Python", "ReactJS", "Java"], 6),
    cv(2, "bo", ["Python"], 2, text="Built tools in JavaScript"),  # 'JavaScript' must not count as Java
    cv(3, "cy", ["Excel"], 10),  # no must-have skill -> excluded
    cv(4, "ana", ["Python", "React", "Java"], 6),  # same person again -> counted once
    cv(5, "di", ["Python"], 3, text="Shipped React apps with Docker"),  # skills found in CV text
])
assert [m["name"] for m in ranked] == ["ana", "di", "bo"], ranked
assert ranked[0]["score"] == 90 and ranked[0]["missing_skills"] == []  # 60 + 0 (no Docker) + 30
assert ranked[1]["matched_skills"] == ["Python", "React"] and ranked[1]["nice_matched"] == ["Docker"]
assert ranked[2]["missing_skills"] == ["React", "Java"]

print("ok")
