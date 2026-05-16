from __future__ import annotations

import logging

import asyncio
import json
import re
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .downloader import download_file, download_path
from .kindle_sync import send_to_kindle
from .models import DownloadRequest, DownloadResult, KindleSyncRequest, KindleSyncResult, LibraryFile, LibraryResponse, RenameRequest, SearchResponse
from .scraper import get_download_url, search_with_fallback

app = FastAPI(title="Bookstore API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.backend_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

_ANNAS_HOST_RE = re.compile(r"^annas-archive\.(gl|org|se|st)$")


def _valid_annas_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return bool(_ANNAS_HOST_RE.match(host))


@app.get("/search", response_model=SearchResponse)
async def search(q: str | None = Query(default=None)):
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail={"error": "Query parameter 'q' is required"})

    logger.info("[api] GET /search q=%r", q)
    try:
        results, fmt = await search_with_fallback(q.strip())
    except Exception:
        logger.exception("[api] search_with_fallback raised for q=%r", q)
        return JSONResponse(status_code=500, content={"error": "Search failed — please retry"})

    logger.info("[api] /search done | q=%r fmt=%s results=%d", q, fmt, len(results))
    return SearchResponse(results=results, query=q.strip(), format_used=fmt, total=len(results))


@app.post("/download", response_model=DownloadResult)
async def download(req: DownloadRequest):
    if not _valid_annas_url(req.detail_url):
        raise HTTPException(status_code=400, detail={"error": "Invalid detail_url domain"})

    try:
        dl_url = await get_download_url(md5=req.md5, detail_url=req.detail_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})
    except Exception:
        logger.exception("[api] get_download_url failed for md5=%r", req.md5)
        return JSONResponse(status_code=500, content={"error": "Download failed — please retry"})

    preferred: str | None = None
    if req.title:
        ext = req.format or "azw3"
        from .downloader import safe_filename
        preferred = f"{safe_filename(req.title)}.{ext}"

    try:
        filepath, filename = await download_file(dl_url, preferred_filename=preferred)
    except RuntimeError as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
    except Exception:
        logger.exception("[api] download_file failed for md5=%r", req.md5)
        return JSONResponse(status_code=500, content={"error": "Download failed — please retry"})

    return DownloadResult(
        success=True,
        filename=filename,
        filepath=str(filepath),
    )


@app.post("/download-stream")
async def download_stream(req: DownloadRequest):
    if not _valid_annas_url(req.detail_url):
        raise HTTPException(status_code=400, detail={"error": "Invalid detail_url domain"})

    queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()

    async def emit(msg: str) -> None:
        await queue.put(("progress", {"step": msg}))

    async def run() -> None:
        try:
            dl_url = await get_download_url(md5=req.md5, detail_url=req.detail_url, emit=emit)
            await queue.put(("progress", {"step": "Downloading file…"}))

            preferred: str | None = None
            if req.title:
                ext = req.format or "azw3"
                from .downloader import safe_filename
                preferred = f"{safe_filename(req.title)}.{ext}"

            filepath, filename = await download_file(dl_url, preferred_filename=preferred)
            await queue.put(("done", {"success": True, "filename": filename, "filepath": str(filepath)}))
        except (ValueError, RuntimeError) as exc:
            await queue.put(("error", {"error": str(exc)}))
        except Exception:
            logger.exception("[api] download_stream failed for md5=%r", req.md5)
            await queue.put(("error", {"error": "Download failed — please retry"}))

    asyncio.create_task(run())

    async def event_gen():
        while True:
            event_type, data = await queue.get()
            yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
            if event_type in ("done", "error"):
                break

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.get("/library", response_model=LibraryResponse)
async def library():
    base = settings.download_dir
    files: list[LibraryFile] = []
    if base.exists():
        for f in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.is_file():
                stat = f.stat()
                size = stat.st_size
                if size >= 1_048_576:
                    human = f"{size / 1_048_576:.1f} MB"
                elif size >= 1024:
                    human = f"{size / 1024:.1f} KB"
                else:
                    human = f"{size} B"
                from datetime import datetime, timezone
                modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
                files.append(LibraryFile(filename=f.name, size_bytes=size, size_human=human, modified_at=modified))
    return LibraryResponse(files=files, total=len(files))


@app.post("/sync-kindle", response_model=KindleSyncResult)
async def sync_kindle(req: KindleSyncRequest):
    try:
        filepath = download_path(req.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})

    if not filepath.exists():
        raise HTTPException(status_code=404, detail={"error": f"{req.filename!r} not found in downloads"})

    logger.info("[api] POST /sync-kindle filename=%r", req.filename)
    sent, err = send_to_kindle(filepath)
    return KindleSyncResult(sent=sent, error=err)


@app.delete("/library/{filename}")
async def delete_library_file(filename: str):
    try:
        filepath = download_path(filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})

    if not filepath.exists():
        raise HTTPException(status_code=404, detail={"error": f"{filename!r} not found in downloads"})

    logger.info("[api] DELETE /library/%s", filename)
    filepath.unlink()
    return {"deleted": True}


@app.patch("/library/{filename}")
async def rename_library_file(filename: str, req: RenameRequest):
    try:
        src_path = download_path(filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})

    if not src_path.exists():
        raise HTTPException(status_code=404, detail={"error": f"{filename!r} not found in downloads"})

    try:
        dst_path = download_path(req.new_filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})

    if dst_path.exists():
        raise HTTPException(status_code=409, detail={"error": f"{req.new_filename!r} already exists"})

    logger.info("[api] PATCH /library/%s → %s", filename, req.new_filename)
    src_path.rename(dst_path)
    return {"filename": req.new_filename}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    detail = exc.detail
    if isinstance(detail, dict):
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(status_code=exc.status_code, content={"error": detail})
