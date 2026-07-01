"""
Document ingestion: walk a folder, parse PDF/DOCX/TXT, return plain text.
Failures are logged and skipped — never crash the batch.
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}


def extract_text_from_pdf(path: Path) -> str:
    import pdfplumber

    text_parts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
    return "\n".join(text_parts)


def extract_text_from_docx(path: Path) -> str:
    from docx import Document

    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def extract_text_from_txt(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def parse_document(path: Path) -> str | None:
    """
    Parse a single document. Returns plain text or None on failure.
    """
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            return extract_text_from_pdf(path)
        elif suffix == ".docx":
            return extract_text_from_docx(path)
        elif suffix == ".txt":
            return extract_text_from_txt(path)
        else:
            logger.warning(f"Unsupported file type, skipping: {path}")
            return None
    except Exception as e:
        logger.error(f"Failed to parse {path}: {e}")
        return None


def ingest_folder(folder: Path) -> list[tuple[Path, str]]:
    """
    Walk folder recursively, parse all supported documents.
    Returns list of (path, text) tuples for successfully parsed files.
    Skips unsupported types and logs parse failures without crashing.
    """
    results = []
    files = [
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not files:
        logger.warning(f"No supported documents found in {folder}")
        return results

    logger.info(f"Found {len(files)} document(s) in {folder}")

    for path in sorted(files):
        text = parse_document(path)
        if text and text.strip():
            results.append((path, text))
        elif text is not None:
            logger.warning(f"Parsed but empty content, skipping: {path}")

    logger.info(f"Successfully parsed {len(results)}/{len(files)} document(s)")
    return results
