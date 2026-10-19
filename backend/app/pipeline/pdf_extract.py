"""PDF text extraction for the RAG pipeline.

Uses pypdf for text-layer extraction only. Scanned/image-only PDFs need an
OCR step before this module (e.g. Tesseract or a hosted OCR service) — OCR
is explicitly out of scope here: pages whose text layer is empty are kept
with an empty string so downstream chunking keeps page numbering aligned.

Only stdlib + pypdf are imported at module level. No app-internal imports
happen here at all, so this module never pulls config/db/ingestion.
"""

from __future__ import annotations

import os


def extract_pages(pdf_path: str) -> list:
    """Extract text from each page of a PDF.

    Returns a list of ``{"page": <1-based int>, "text": <str>}`` dicts.
    Pages with no extractable text are kept with ``""`` so page numbers
    stay aligned with the source document. Encrypted PDFs are retried
    with an empty password.

    Raises:
        FileNotFoundError: if ``pdf_path`` does not exist.
    """
    if not pdf_path or not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path!r}")

    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    if reader.is_encrypted:
        # Try the empty password. NOTE (pypdf>=6): is_encrypted stays True
        # even after a successful decrypt, so the return value is the check:
        # 0 == NOT_DECRYPTED, 1/2 == user/owner password accepted.
        try:
            decrypted = reader.decrypt("")
        except Exception:
            decrypted = 0
        if decrypted == 0:
            raise ValueError(
                f"PDF is encrypted and the empty password failed: {pdf_path!r} "
                "(decryption credentials are out of scope for this module)"
            )

    pages = []
    for idx, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        pages.append({"page": idx, "text": text})
    return pages
