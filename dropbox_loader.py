"""
dropbox_loader.py — Loads resume / portfolio files from Dropbox into text.
Supports PDF and DOCX. Falls back to plain text for .txt files.
Caches content in memory to avoid re-fetching on every message.
"""

import io
import logging
import time
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)

# Optional imports — only required if Dropbox is configured
try:
    import dropbox as dbx_sdk
    DROPBOX_AVAILABLE = True
except ImportError:
    DROPBOX_AVAILABLE = False
    logger.warning("dropbox SDK not installed. Run: pip install dropbox")

try:
    import pdfplumber
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False
    logger.warning("pdfplumber not installed. Run: pip install pdfplumber")

try:
    from docx import Document as DocxDocument
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
    logger.warning("python-docx not installed. Run: pip install python-docx")


class DropboxResumeLoader:
    """
    Loads one or more files from Dropbox and caches the parsed text.
    Refreshes the cache every `refresh_hours` hours.

    Usage:
        loader = DropboxResumeLoader(token="...", paths=["/resume.pdf", "/portfolio.docx"])
        await loader.load()
        text = loader.get_combined_text()
    """

    def __init__(self, token: str, paths: list[str], refresh_hours: float = 6.0):
        if not DROPBOX_AVAILABLE:
            raise RuntimeError("dropbox SDK not installed. Run: pip install dropbox")
        self._dbx            = dbx_sdk.Dropbox(token)
        self._paths          = paths
        self._refresh_secs   = refresh_hours * 3600
        self._cache: dict[str, str] = {}
        self._last_load: float = 0.0

    def _is_stale(self) -> bool:
        return (time.time() - self._last_load) > self._refresh_secs

    def load(self) -> None:
        """
        Synchronous load — call once on startup and again when cache is stale.
        python-telegram-bot v21 uses asyncio; call this in a thread or at startup.
        """
        if not self._is_stale():
            return
        logger.info("Loading %d file(s) from Dropbox...", len(self._paths))
        loaded = 0
        for path in self._paths:
            try:
                _, response = self._dbx.files_download(path)
                text = self._parse(path, response.content)
                self._cache[path] = text
                loaded += 1
                logger.info("Loaded %s (%d chars)", path, len(text))
            except Exception as exc:
                logger.error("Failed to load %s from Dropbox: %s", path, exc)
                self._cache[path] = f"[Could not load {path}: {exc}]"
        self._last_load = time.time()
        logger.info("Dropbox load complete — %d/%d files loaded.", loaded, len(self._paths))

    def _parse(self, path: str, content: bytes) -> str:
        ext = PurePosixPath(path).suffix.lower()
        if ext == ".pdf":
            return self._parse_pdf(content)
        elif ext == ".docx":
            return self._parse_docx(content)
        else:
            return content.decode("utf-8", errors="replace")

    def _parse_pdf(self, content: bytes) -> str:
        if not PDF_AVAILABLE:
            return "[PDF parsing unavailable — install pdfplumber]"
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        return "\n\n".join(p.strip() for p in pages if p.strip())

    def _parse_docx(self, content: bytes) -> str:
        if not DOCX_AVAILABLE:
            return "[DOCX parsing unavailable — install python-docx]"
        doc = DocxDocument(io.BytesIO(content))
        return "\n".join(para.text for para in doc.paragraphs if para.text.strip())

    def get_combined_text(self) -> str:
        """Returns all loaded file content merged into a single string."""
        if not self._cache:
            return ""
        parts = []
        for path, text in self._cache.items():
            filename = PurePosixPath(path).name
            parts.append(f"=== {filename} ===\n{text}")
        return "\n\n".join(parts)

    @property
    def is_loaded(self) -> bool:
        return bool(self._cache)