"""Generates fake resumes in different layouts, plus deliberately bad files, into data/sample_resumes/.
Run once:  python tests/make_samples.py
"""
from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parent.parent / "data" / "sample_resumes"
OUT.mkdir(parents=True, exist_ok=True)


def single_column(fn, name, email, phone, body):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 60), name, fontsize=20)
    page.insert_text((50, 82), f"{email} | {phone}", fontsize=10)
    page.insert_textbox(pymupdf.Rect(50, 100, 545, 800), body, fontsize=10)
    doc.save(OUT / fn)


def two_column(fn, name, email, phone, sidebar, main):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((40, 50), name.upper(), fontsize=22)
    page.insert_textbox(pymupdf.Rect(40, 80, 200, 800), f"CONTACT\n{email}\n{phone}\n\n{sidebar}", fontsize=10)
    page.insert_textbox(pymupdf.Rect(230, 80, 560, 800), main, fontsize=10)
    doc.save(OUT / fn)


single_column(
    "synthetic_priya_sharma.pdf", "Priya Sharma", "priya.sharma@example.com", "+91 98765 43210",
    """SUMMARY
ML engineer with 4 years of experience building production ML services.

EXPERIENCE
Machine Learning Engineer, DataWorks (2021 - 2025)
- Built FastAPI services serving TensorFlow models; containerised with Docker.
- Improved recommendation accuracy by 12%.

EDUCATION
B.Tech Computer Science, VIT University, 2020

SKILLS
Python, FastAPI, TensorFlow, Machine Learning, Docker, PostgreSQL, Git

PROJECTS
Resume Ranker: NLP pipeline to rank resumes.

CERTIFICATIONS
TensorFlow Developer Certificate""",
)

two_column(
    "synthetic_rahul_verma.pdf", "Rahul Verma", "rahul.v@example.com", "+91 91234 56789",
    "SKILLS\nJava\nSpring Boot\nMySQL\nPython\nSQL\n\nEDUCATION\nB.E. IT, Pune University, 2022",
    """EXPERIENCE
Software Engineer, FinServe (2022 - 2024)
Developed REST APIs in Java and Spring Boot.
Wrote Python scripts for data cleanup.
Total experience: 2 years.

PROJECTS
Inventory Manager - Spring Boot + MySQL.""",
)

single_column(
    "synthetic_ananya_iyer.pdf", "ANANYA IYER", "ananya.iyer@example.com", "9876501234",
    """PROFILE: Fresher, recent graduate interested in data science.

EDUCATION: M.Sc. Data Science, 2025

TECHNICAL SKILLS: Python, Pandas, Scikit-learn, SQL, Deep Learning

PROJECTS: Sentiment analysis of tweets; house price prediction (regression).

CERTIFICATIONS: Coursera Machine Learning Specialization""",
)

# Scanned resume: render a text page to an image, then wrap the image in a new PDF (no text layer).
src = pymupdf.open(OUT / "synthetic_ananya_iyer.pdf")
pix = src[0].get_pixmap(dpi=100)
scan = pymupdf.open()
scan.new_page(width=595, height=842).insert_image(pymupdf.Rect(0, 0, 595, 842), pixmap=pix)
scan.save(OUT / "SCANNED_bad_resume.pdf")

(OUT / "CORRUPT_bad_resume.pdf").write_bytes(b"this is not really a pdf")
(OUT / "UNSUPPORTED_resume.txt").write_text("Plain text resume, not a PDF.")
print("Sample files written to", OUT)
