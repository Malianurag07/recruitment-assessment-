"""Single entry point for uploads: routes by file extension to the right extractor."""
from pathlib import Path

from app.parsing.docx_extractor import extract_docx
from app.parsing.pdf_extractor import ExtractionResult, extract_text as extract_pdf

SUPPORTED = (".pdf", ".docx")


def extract_document(data: bytes, filename: str) -> ExtractionResult:
    name = filename.lower()
    if name.endswith(".pdf"):
        return extract_pdf(data, filename)
    if name.endswith(".docx"):
        return extract_docx(data, filename)
    return ExtractionResult(filename, "unsupported", message="Supported formats: PDF, DOCX.")


def extract_from_path(path: str | Path) -> ExtractionResult:
    path = Path(path)
    return extract_document(path.read_bytes(), path.name)
