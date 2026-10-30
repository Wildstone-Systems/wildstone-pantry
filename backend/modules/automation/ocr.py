from __future__ import annotations

_PDF_MAGIC = b"%PDF-"


def image_bytes_to_text(payload: bytes) -> str:
    """Optional OCR wrapper; requires pytesseract + pillow at runtime."""
    try:
        from io import BytesIO

        import pytesseract
        from PIL import Image, UnidentifiedImageError
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("OCR dependencies not installed") from exc

    try:
        image = Image.open(BytesIO(payload))
    except UnidentifiedImageError as exc:
        raise ValueError("Invalid image upload") from exc

    return pytesseract.image_to_string(image)


def pdf_bytes_to_text(payload: bytes) -> str:
    """
    Extract text from a PDF.

    Most recipe PDFs (printed from a browser, exported from a notes app) carry a
    real text layer, which needs no OCR at all and is far more accurate than it.
    Only when the text layer is empty — a scan or a photo saved as PDF — do we
    fall back to rasterising and OCRing each page, which needs poppler.
    """

    try:
        from io import BytesIO

        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("PDF dependencies not installed") from exc

    try:
        reader = PdfReader(BytesIO(payload))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        raise ValueError("Invalid or unreadable PDF upload") from exc

    text = "\n".join(pages).strip()
    if text:
        return text

    return _scanned_pdf_to_text(payload)


def _scanned_pdf_to_text(payload: bytes) -> str:
    """OCR a PDF with no text layer. Requires pdf2image + poppler."""

    try:
        from pdf2image import convert_from_bytes
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "This PDF has no text layer and OCR for scanned PDFs is unavailable "
            "(pdf2image/poppler not installed). Upload a photo of the recipe instead."
        ) from exc

    try:
        import pytesseract
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("OCR dependencies not installed") from exc

    try:
        images = convert_from_bytes(payload)
    except Exception as exc:
        raise ValueError("Invalid or unreadable PDF upload") from exc

    return "\n".join(pytesseract.image_to_string(image) for image in images).strip()


def document_bytes_to_text(payload: bytes, *, filename: str | None = None, content_type: str | None = None) -> str:
    """
    Turn an uploaded recipe document into text, picking the right extractor.

    Sniffs the PDF magic bytes first — a phone "scan to PDF" often arrives with
    a generic content type, and the filename may be missing entirely.
    """

    looks_like_pdf = (
        payload[:5] == _PDF_MAGIC
        or (content_type or "").lower() == "application/pdf"
        or (filename or "").lower().endswith(".pdf")
    )

    if looks_like_pdf:
        return pdf_bytes_to_text(payload)

    return image_bytes_to_text(payload)
