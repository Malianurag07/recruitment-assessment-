from pathlib import Path

import pytest

from app.parsing.pdf_extractor import extract_from_path, extract_text

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "sample_resumes"


@pytest.mark.parametrize("fn,must_contain", [
    ("synthetic_priya_sharma.pdf", ["priya.sharma@example.com", "FastAPI", "Docker"]),
    ("synthetic_rahul_verma.pdf", ["rahul.v@example.com", "Spring Boot", "MySQL"]),
    ("synthetic_ananya_iyer.pdf", ["ananya.iyer@example.com", "Scikit-learn"]),
])
def test_good_resumes_extract(fn, must_contain):
    r = extract_from_path(SAMPLES / fn)
    assert r.ok
    for word in must_contain:
        assert word in r.text


def test_scanned_pdf_flagged():
    assert extract_from_path(SAMPLES / "SCANNED_bad_resume.pdf").status == "empty_or_scanned"


def test_corrupt_pdf_flagged():
    assert extract_from_path(SAMPLES / "CORRUPT_bad_resume.pdf").status == "corrupt"


def test_non_pdf_flagged():
    assert extract_from_path(SAMPLES / "UNSUPPORTED_resume.txt").status == "unsupported"


def test_empty_bytes():
    assert extract_text(b"", "x.pdf").status == "corrupt"


# --- real-world resumes + docx ---
from app.parsing.document_extractor import extract_document, extract_from_path as extract_any


def test_all_real_resumes_extract():
    real = ["anurag_ibm_swe.docx", "anurag_hr.pdf", "anurag_aiml.pdf", "anurag_python_junior.pdf",
            "jeevan_raj.pdf", "ananya_rk_testing.pdf", "kushal_br.pdf"]
    for fn in real:
        r = extract_any(SAMPLES / fn)
        assert r.ok, (fn, r.status, r.message)


def test_docx_contains_key_content():
    r = extract_any(SAMPLES / "anurag_ibm_swe.docx")
    assert "malianuraghr07@gmail.com" in r.text and "Next.js" in r.text


def test_docx_bad_files():
    assert extract_document(b"not a zip", "x.docx").status == "corrupt"
    assert extract_document(b"x", "x.doc").status == "unsupported"
