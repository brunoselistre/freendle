from __future__ import annotations

import ipaddress
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import settings

DOWNLOAD_DIR_CAP_BYTES = 1 * 1024 ** 3   # 1 GB total
MAX_FILE_BYTES = 500 * 1024 ** 2          # 500 MB per file


def _dir_size_bytes() -> int:
    base = settings.download_dir
    if not base.exists():
        return 0
    return sum(f.stat().st_size for f in base.iterdir() if f.is_file())


def check_disk_cap() -> None:
    used = _dir_size_bytes()
    if used >= DOWNLOAD_DIR_CAP_BYTES:
        used_gb = used / 1024 ** 3
        raise RuntimeError(
            f"Downloads folder is full ({used_gb:.1f} GB / 1 GB limit). "
            "Delete some books to free space."
        )


def validate_download_url(url: str) -> None:
    """Block non-HTTPS and private/internal hosts (SSRF guard)."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Download URL must use HTTPS, got {parsed.scheme!r}")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("Download URL has no hostname")
    try:
        addr = socket.gethostbyname(host)
        ip = ipaddress.ip_address(addr)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(f"Download URL resolves to non-public address ({addr}) — blocked")
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve download host {host!r}: {exc}")


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
            return match.group(1).strip()[:255]
    return (Path(url.split("?")[0]).name or "download")[:255]


async def download_file(url: str, preferred_filename: str | None = None) -> tuple[Path, str]:
    """Download url to sandbox. Returns (filepath, filename).

    preferred_filename: if given, use it instead of Content-Disposition / URL basename.
    """
    validate_download_url(url)
    check_disk_cap()

    async with httpx.AsyncClient(follow_redirects=True, timeout=300) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            if preferred_filename:
                filename = preferred_filename
            else:
                filename = _filename_from_headers(response.headers, url)
            dest = download_path(filename)
            written = 0
            with dest.open("wb") as fh:
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    written += len(chunk)
                    if written > MAX_FILE_BYTES:
                        dest.unlink(missing_ok=True)
                        raise RuntimeError(
                            f"File exceeds {MAX_FILE_BYTES // 1024 ** 2} MB limit — download aborted"
                        )
                    fh.write(chunk)
    return dest, dest.name
