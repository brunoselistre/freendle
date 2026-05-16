from __future__ import annotations

import re
from pathlib import Path

import httpx

from .config import settings


def safe_filename(name: str) -> str:
    """Sanitize a filename, keeping alphanumerics, spaces, dots, hyphens, and underscores."""
    name = re.sub(r"[^\w\s\-.]", "", name)
    name = re.sub(r" +", " ", name)
    return name.strip()


def download_path(filename: str) -> Path:
    """Resolve filename to an absolute path inside DOWNLOAD_DIR.

    Raises ValueError if the filename contains path traversal patterns or
    the resolved path escapes the sandbox.
    """
    base = settings.download_dir.resolve()
    base.mkdir(parents=True, exist_ok=True)

    if filename.startswith("/") or ".." in filename.replace("\\", "/").split("/"):
        raise ValueError(f"Filename {filename!r} is outside download sandbox {base}")

    safe = safe_filename(Path(filename).name)
    candidate = (base / safe).resolve()

    if not candidate.is_relative_to(base):
        raise ValueError(f"Resolved path {candidate} is outside download sandbox {base}")

    return candidate


def _filename_from_headers(headers: httpx.Headers, url: str) -> str:
    cd = headers.get("content-disposition", "")
    if cd:
        match = re.search(r'filename\*?=["\']?(?:UTF-8\'\')?([^"\';\n]+)', cd, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return Path(url.split("?")[0]).name or "download"


async def download_file(url: str, preferred_filename: str | None = None) -> tuple[Path, str]:
    """Download url to sandbox. Returns (filepath, filename).

    preferred_filename: if given, use it instead of Content-Disposition / URL basename.
    """
    async with httpx.AsyncClient(follow_redirects=True, timeout=300) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            if preferred_filename:
                filename = preferred_filename
            else:
                filename = _filename_from_headers(response.headers, url)
            dest = download_path(filename)
            with dest.open("wb") as fh:
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    fh.write(chunk)
    return dest, dest.name
