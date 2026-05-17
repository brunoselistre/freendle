"""Anna's Archive scraper.

CSS selectors discovered from Anna's Archive search results HTML (2024-05).
Result rows are `.js-vim-focus` divs; within each row:
- Title:      .text-xl.font-bold a  (first anchor with bold title)
- Author:     .italic               (italic span — may be absent)
- Format/Size/Language: .text-xs.text-gray-500 children — comma-separated metadata
- MD5/URL:    .js-vim-focus a[href^="/md5/"]  (first href starting with /md5/)

Download panel selector (detail page):
  #md5-panel-downloads > div:nth-child(2) > ul > li:nth-child(N) > a  (N=1..5)
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Callable, Awaitable
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from .models import SearchResult, BookFormat

logger = logging.getLogger(__name__)

# ── Search page selectors ────────────────────────────────────────────────────
SEL_RESULT_ROW = ".js-vim-focus"
SEL_TITLE_LINK = "h3 a, .text-xl a, a.font-bold"
SEL_AUTHOR = ".italic"
SEL_META = ".text-xs.text-gray-500"
SEL_MD5_LINK = 'a[href^="/md5/"]'

# ── Download panel selectors (detail page) ───────────────────────────────────
# Select ALL uls in the panel — site structure varies by file type/status
SEL_DOWNLOAD_UL = "#md5-panel-downloads ul"
FAST_LINKS_COUNT = 5  # last N li items — no wait page
# ── Slow-download intermediate page — multiple fallbacks (site structure varies)
SEL_SLOW_DOWNLOAD_CANDIDATES = [
    "body > main > div > p.mb-4.text-xl.font-bold > a",
    "main p.font-bold > a[href^='http']",
    "main .mb-4 > a[href^='http']",
    "main a[href*='/dl/']",
    "main a[href*='download']",
]
LINK_TIMEOUT_S = 30

_FILE_URL_RE = re.compile(r"^https://\S+$")  # https only — blocks http/file/internal

ANNAS_BASE = "https://annas-archive.gl"
FALLBACK_ORDER: list[BookFormat] = ["pdf", "epub", "azw3"]

# Allowed annas-archive hostnames — guards detail_url validation
_ANNAS_HOST_RE = re.compile(r"^annas-archive\.(gl|org|se|st)$")

_STEALTH_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_STEALTH_ARGS = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
_STEALTH_INIT = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"


async def _stealth_page(browser):
    context = await browser.new_context(
        user_agent=_STEALTH_UA,
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    await context.add_init_script(_STEALTH_INIT)
    page = await context.new_page()
    page.set_default_timeout(4500)
    return page


def _parse_format(text: str) -> BookFormat | None:
    t = text.lower()
    for fmt in FALLBACK_ORDER:
        if fmt in t:
            return fmt  # type: ignore[return-value]
    return None


async def search_books(query: str, ext: str) -> list[dict]:
    """Return raw dicts from one search page (one format)."""
    url = (
        f"{ANNAS_BASE}/search"
        f"?index=&ext={ext}&page=1&sort=&display=&q={query}"
    )
    results: list[dict] = []
    logger.info("[scraper] search_books start | query=%r ext=%s url=%s", query, ext, url)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=_STEALTH_ARGS)
        page = await _stealth_page(browser)
        try:
            logger.info("[scraper] goto %s", url)
            await page.goto(url, wait_until="domcontentloaded")
            page_title = await page.title()
            logger.info("[scraper] page loaded | title=%r", page_title)

            logger.info("[scraper] waiting for selector %r", SEL_RESULT_ROW)
            await page.wait_for_selector(SEL_RESULT_ROW, state="attached", timeout=4500)
            rows = await page.query_selector_all(SEL_RESULT_ROW)
            logger.info("[scraper] found %d result rows", len(rows))

            for i, row in enumerate(rows):
                # .js-vim-focus IS the <a href="/md5/..."> element — get href directly
                href = await row.get_attribute("href") or ""
                md5_match = re.search(r"/md5/([a-fA-F0-9]+)", href)
                if not md5_match:
                    if i < 3:
                        logger.info("[scraper] row %d: href=%r no md5, skipping", i, href)
                    continue
                md5 = md5_match.group(1)
                detail_url = f"{ANNAS_BASE}{href}"

                # Full text of the row — title, author, format all in inner_text
                row_text = (await row.inner_text()).strip()
                if i == 0:
                    logger.info("[scraper] ROW_0_TEXT=%r", row_text[:300])

                # Title: everything before " - " or first newline
                parts = [p.strip() for p in row_text.split("\n") if p.strip()]
                title = parts[0] if parts else md5

                # Author (may be absent)
                author_el = await row.query_selector(SEL_AUTHOR)
                author = (await author_el.inner_text()).strip() if author_el else ""

                # Meta: format, size, language from small text blocks
                meta_el = await row.query_selector(SEL_META)
                meta_text = (await meta_el.inner_text()).strip() if meta_el else row_text
                meta_parts = [p.strip() for p in meta_text.split("\n") if p.strip()]

                fmt = _parse_format(meta_text) or ext  # type: ignore[assignment]
                size = next((p for p in meta_parts if re.search(r"\d+\s*[KMG]B", p, re.I)), "")
                language = next(
                    (p for p in meta_parts if re.match(r"^[A-Za-z]{2,30}$", p) and p.lower() not in ("pdf", "epub", "azw3", "mobi")),
                    "",
                )

                item = dict(
                    md5=md5,
                    title=title,
                    author=author,
                    format=fmt,
                    size=size,
                    language=language,
                    detail_url=detail_url,
                )
                logger.debug("[scraper] row %d parsed: %s", i, item)
                results.append(item)

        except Exception:
            logger.exception("[scraper] search_books FAILED | query=%r ext=%s", query, ext)
        finally:
            await browser.close()

    logger.info("[scraper] search_books done | query=%r ext=%s results=%d", query, ext, len(results))
    return results


async def search_with_fallback(query: str) -> tuple[list[SearchResult], BookFormat | None]:
    """Try formats in FALLBACK_ORDER; return first non-empty result set."""
    for fmt in FALLBACK_ORDER:
        raw = await search_books(query, fmt)
        logger.info("[scraper] fallback | fmt=%s raw_count=%d", fmt, len(raw))
        if raw:
            valid: list[SearchResult] = []
            for item in raw:
                try:
                    valid.append(SearchResult(**item))
                except Exception as exc:
                    logger.warning("[scraper] SearchResult validation failed | item=%s error=%s", item, exc)
            logger.info("[scraper] fallback | fmt=%s valid=%d dropped=%d", fmt, len(valid), len(raw) - len(valid))
            if valid:
                return valid, fmt  # type: ignore[return-value]
    return [], None


Emitter = Callable[[str], Awaitable[None]]


async def get_download_url(md5: str, detail_url: str, emit: Emitter | None = None) -> str:
    """Navigate to detail page, try each download link (up to MAX_DOWNLOAD_LINKS).

    Each slow_download link gets LINK_TIMEOUT_S seconds before moving on.
    Security: validates domain and md5/URL consistency before navigating.
    """
    host = urlparse(detail_url).hostname or ""
    if not _ANNAS_HOST_RE.match(host):
        raise ValueError(f"detail_url host not in allowed list, got {host!r}")

    url_md5_match = re.search(r"/md5/([a-fA-F0-9]+)", detail_url)
    if not url_md5_match or url_md5_match.group(1).lower() != md5.lower():
        raise ValueError(
            f"md5 mismatch: request md5={md5!r} but detail_url contains {url_md5_match and url_md5_match.group(1)!r}"
        )

    async def _emit(msg: str) -> None:
        logger.info("[scraper] %s", msg)
        if emit:
            await emit(msg)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=_STEALTH_ARGS)
        page = await _stealth_page(browser)
        try:
            await _emit("Opening detail page…")
            # Warm up DDoS-Guard cookies on root domain before hitting /slow_download/
            await page.goto(ANNAS_BASE, wait_until="domcontentloaded")
            await page.goto(detail_url, wait_until="domcontentloaded")

            # Collect all download hrefs from panel — prefer slow (free) over fast (membership)
            slow_hrefs: list[str] = []
            fast_hrefs: list[str] = []
            uls = await page.query_selector_all(SEL_DOWNLOAD_UL)
            for ul in uls:
                items = await ul.query_selector_all("li > a")
                for el in items:
                    href = await el.get_attribute("href") or ""
                    if not href:
                        continue
                    if href.startswith("/"):
                        href = f"{ANNAS_BASE}{href}"
                    if "/fast_download/" in href:
                        fast_hrefs.append(href)
                    elif "/slow_download/" in href or "/downloads/" in href:
                        slow_hrefs.append(href)
                    else:
                        slow_hrefs.append(href)
            hrefs = slow_hrefs + fast_hrefs
            logger.info(
                "[scraper] found %d download hrefs in panel (slow=%d fast=%d)",
                len(hrefs), len(slow_hrefs), len(fast_hrefs),
            )

            if not hrefs:
                panel = await page.query_selector("#md5-panel-downloads")
                if panel:
                    html = (await panel.inner_html())[:3000]
                    if "fast_download" in html and not slow_hrefs:
                        logger.error(
                            "[scraper] only fast-download links found (requires membership). "
                            "File may be spam-flagged or restricted. HTML:\n%s", html,
                        )
                        raise RuntimeError("File requires membership — only fast download links available")
                    logger.error("[scraper] panel found but ul/links empty. HTML:\n%s", html)
                else:
                    logger.error("[scraper] #md5-panel-downloads NOT FOUND — page links:")
                    for lnk in (await page.query_selector_all("a[href]"))[:20]:
                        logger.error("[scraper]  href=%r text=%r", await lnk.get_attribute("href"), (await lnk.inner_text()).strip()[:80])
                raise RuntimeError("No download links found in panel")

            await _emit(f"Found {len(hrefs)} download link(s)")

            for i, href in enumerate(hrefs, 1):
                await _emit(f"Trying link {i}/{len(hrefs)}…")
                result = await _try_link(page, detail_url, href, i, len(hrefs), _emit)
                if result:
                    return result

            raise RuntimeError("All download links failed or timed out")
        finally:
            await browser.close()


async def _try_link(
    page,
    detail_url: str,
    href: str,
    link_n: int,
    total: int,
    emit: Emitter,
) -> str | None:
    try:
        response = await page.goto(href, wait_until="commit")
    except Exception as exc:
        logger.warning("[scraper] link %d/%d goto failed | %s: %s", link_n, total, type(exc).__name__, exc)
        return None
    await asyncio.sleep(1)

    if response:
        ct = response.headers.get("content-type", "")
        cd = response.headers.get("content-disposition", "")
        status = response.status
        logger.info("[scraper] link %d/%d response | status=%d ct=%r cd=%r url=%s", link_n, total, status, ct, cd, response.url)
        if "text/html" not in ct and "attachment" not in cd:
            final_url = str(response.url)
            logger.info("[scraper] link %d/%d direct file response", link_n, total)
            return final_url
    else:
        logger.warning("[scraper] link %d/%d no response object", link_n, total)

    deadline = time.monotonic() + LINK_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            for sel in SEL_SLOW_DOWNLOAD_CANDIDATES:
                real_el = await page.query_selector(sel)
                if real_el:
                    href_attr = await real_el.get_attribute("href") or ""
                    if href_attr.startswith("/"):
                        href_attr = f"https://annas-archive.gl{href_attr}"
                    if _FILE_URL_RE.match(href_attr):
                        logger.info("[scraper] link %d/%d real_url=%s (sel=%r)", link_n, total, href_attr, sel)
                        return href_attr
        except Exception as exc:
            logger.warning("[scraper] link %d/%d context error | %s: %s", link_n, total, type(exc).__name__, exc)
        elapsed = int(LINK_TIMEOUT_S - (deadline - time.monotonic()))
        await emit(f"Link {link_n}/{total}: waiting… {elapsed}s / {LINK_TIMEOUT_S}s")
        await asyncio.sleep(1)

    # Log page state so we can improve selectors
    try:
        current_url = page.url
    except Exception:
        current_url = "<unavailable>"
    try:
        html = (await page.content())[:2000]
    except Exception as e:
        html = f"<error: {e}>"
    logger.warning(
        "[scraper] link %d/%d timed out | url=%s | html=%r",
        link_n, total, current_url, html,
    )
    await emit(f"Link {link_n}/{total}: timed out, trying next…")
    try:
        await page.goto(detail_url, wait_until="domcontentloaded")
    except Exception as exc:
        logger.warning("[scraper] link %d/%d failed to return to detail page | %s: %s", link_n, total, type(exc).__name__, exc)
    return None
