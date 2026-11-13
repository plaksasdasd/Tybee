"""Support-chat files on disk: ``{DATA_DIR}/attachments/{YYYY}/{MM}/{uuid4}{ext}``.

Shared by the bot process (client -> support) and the site (staff -> client, file download).
Rows live in ``support_attachments``; this module only touches the disk and checks signatures.
"""
from __future__ import annotations

import hashlib
import io
import warnings
import mimetypes
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings

MB = 1024 * 1024
TELEGRAM_DOWNLOAD_LIMIT = 20 * MB  # Bot API getFile limit
AVITO_IMAGE_LIMIT = 10 * MB
REPLY_IMAGE_LIMIT = 10 * MB
REPLY_PDF_LIMIT = 20 * MB

INLINE_MIMES = frozenset({"image/jpeg", "image/png", "image/webp"})
IMAGE_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "application/pdf": ".pdf"}


@dataclass(frozen=True)
class StoredFile:
    """A file already written to disk, ready to become a ``SupportAttachment`` row."""

    kind: str
    mime: str
    original_name: str
    size: int
    sha256: str
    path: str  # relative to DATA_DIR
    remote_provider: str | None = None
    remote_id: str | None = None
    remote_cover_id: int | None = None
    remote_profile_id: int | None = None


def data_root() -> Path:
    return Path(get_settings().data_dir).resolve()


def sniff_image(data: bytes) -> str | None:
    """MIME of a JPEG/PNG/WebP by its signature, else None."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_image(data: bytes) -> str | None:
    """Decode a bounded JPEG/PNG/WebP before accepting it, not just its header."""
    mime = sniff_image(data)
    if mime is None:
        return None
    try:
        from PIL import Image
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}[mime]:
                    return None
                if image.width <= 0 or image.height <= 0 or image.width * image.height > 25_000_000:
                    return None
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
        return mime
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return None


def sniff_pdf(data: bytes) -> bool:
    return data.startswith(b"%PDF-")


def _ext(name: str, mime: str) -> str:
    if mime in IMAGE_EXT:
        return IMAGE_EXT[mime]
    suffix = Path(name).suffix.lower()
    if suffix and len(suffix) <= 10 and suffix[1:].isalnum():
        return suffix
    return mimetypes.guess_extension(mime or "") or ""


def safe_name(name: str | None, fallback: str) -> str:
    """Display name only (never used as a path): no directories, no control characters."""
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    base = "".join(ch for ch in base if ch.isprintable())
    return (base or fallback)[:256]


def store_bytes(data: bytes, *, kind: str, mime: str, original_name: str) -> StoredFile:
    """Write ``data`` atomically under ``attachments/YYYY/MM`` and return its description."""
    now = datetime.now(timezone.utc)
    rel = Path("attachments") / f"{now:%Y}" / f"{now:%m}" / f"{uuid.uuid4()}{_ext(original_name, mime)}"
    target = data_root() / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return StoredFile(
        kind=kind,
        mime=mime or "application/octet-stream",
        original_name=original_name,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        path=rel.as_posix(),
    )


def resolve(rel: str | None) -> Path | None:
    """Absolute path of a stored file, or None when it escapes DATA_DIR or does not exist."""
    if not rel:
        return None
    root = data_root()
    try:
        candidate = (root / rel).resolve()
    except (OSError, ValueError):
        return None
    if root not in candidate.parents or not candidate.is_file():
        return None
    return candidate


def discard(files: list[StoredFile]) -> None:
    """Remove files that never made it into the database (e.g. a duplicate event)."""
    for f in files:
        path = resolve(f.path)
        if path is not None:
            try:
                path.unlink()
            except OSError:
                pass
