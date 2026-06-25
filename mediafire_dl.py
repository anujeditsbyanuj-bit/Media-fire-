"""
mediafire_dl.py — Async Mediafire resolver + downloader
Working approach (June 2026):
- Android UA → API calls (Cloudflare bypass)
- Browser UA → HTML page scrape (CDN URL extract)
- Flow:
    1. file/get_info (Android UA) → metadata
    2. file/get_links (Android UA) → normal_download page URL
    3. Browser UA → page GET → CDN URL extract (download[N].mediafire.com)
    4. Android UA → CDN URL se actual file download
"""

import os
import re
import asyncio
import aiohttp
import aiofiles
from typing import Callable, Optional

MF_ANDROID_UA = "MediaFire/5.1 (Android)"
MF_BROWSER_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Mobile Safari/537.36"
)

API_HEADERS = {"User-Agent": MF_ANDROID_UA}
HTML_HEADERS = {
    "User-Agent": MF_BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
DL_HEADERS = {"User-Agent": MF_ANDROID_UA}

BASE_API           = "https://www.mediafire.com/api/1.5"
FILE_GET_INFO      = f"{BASE_API}/file/get_info.php"
FILE_GET_LINKS     = f"{BASE_API}/file/get_links.php"
FOLDER_GET_CONTENT = f"{BASE_API}/folder/get_content.php"

MAX_RETRIES      = 4
RETRY_DELAY      = 2
CHUNK_SIZE       = 524288  # 512 KB
MAX_FOLDER_DEPTH = 10

TIMEOUT_SHORT = aiohttp.ClientTimeout(total=60,   connect=15)
TIMEOUT_DL    = aiohttp.ClientTimeout(total=None, connect=15)


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_folder_link(url: str) -> bool:
    return bool(re.search(r"mediafire\.com/folder/", url, re.I))


def extract_folder_key(url: str) -> str:
    m = re.search(r"mediafire\.com/folder/([a-zA-Z0-9]+)", url, re.I)
    if m:
        return m.group(1)
    h = re.search(r"#([a-zA-Z0-9]+)", url)
    return h.group(1) if h else ""


def extract_file_key(url: str) -> str:
    m = re.search(r"mediafire\.com/file/([a-zA-Z0-9]+)", url, re.I)
    return m.group(1) if m else ""


def _parse_size(val) -> int:
    try:
        return int(float(str(val).replace(",", "").strip()))
    except (ValueError, TypeError):
        return 0


# ── API: JSON fetch (Android UA) ─────────────────────────────────────────────

async def _get_json(session: aiohttp.ClientSession, url: str, timeout=None) -> dict:
    """Android UA se GET → Cloudflare bypass. MAX_RETRIES tak retry karo."""
    last_exc = Exception("Unknown error")
    t = timeout or TIMEOUT_SHORT
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.get(
                url, timeout=t, allow_redirects=True, headers=API_HEADERS
            ) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except Exception as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY * attempt)
    raise last_exc


# ── CDN URL extractor (Browser UA) ───────────────────────────────────────────

async def _extract_cdn_url(session: aiohttp.ClientSession, page_url: str) -> str:
    """
    MediaFire page URL se actual CDN URL extract karo.
    Browser UA se HTML GET → regex se download[N].mediafire.com dhundho.
    """
    last_exc = Exception("CDN URL not found in page HTML")
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.get(
                page_url,
                timeout=TIMEOUT_SHORT,
                allow_redirects=True,
                headers=HTML_HEADERS,
            ) as resp:
                resp.raise_for_status()
                html = await resp.text(errors="ignore")

            patterns = [
                r'(https://download\d*\.mediafire\.com/[^"\'<\s]+)',
                r'"downloadUrl"\s*:\s*"([^"]+)"',
                r"'downloadUrl'\s*:\s*'([^']+)'",
                r'href="(https://download[^"]+mediafire[^"]+)"',
            ]
            for pat in patterns:
                m = re.search(pat, html)
                if m:
                    cdn = m.group(1).replace("\\u002F", "/").replace("\\/", "/")
                    if "mediafire" in cdn:
                        return cdn

            raise Exception("CDN URL not found in page HTML")

        except Exception as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY * attempt)

    raise last_exc


# ── File resolver ─────────────────────────────────────────────────────────────

async def get_info(url: str) -> Optional[dict]:
    """
    Public entry — file URL se CDN URL resolve karo.
    Returns: {'name', 'size', 'url' (CDN), 'key'} or raises Exception.
    FIX: Ab exception propagate hoti hai (pehle silently None return hota tha).
    """
    key = extract_file_key(url)
    if not key:
        raise Exception("Could not extract file key from URL.")
    async with aiohttp.ClientSession() as session:
        return await _resolve_key(session, key)


async def get_file_info_by_key(
    session: aiohttp.ClientSession, key: str
) -> Optional[dict]:
    """
    Folder handler ke liye — shared session pass karo.
    Returns dict or None (exception internally logged, not raised — folder loop continue karta hai).
    """
    try:
        return await _resolve_key(session, key)
    except Exception:
        return None


async def _resolve_key(session: aiohttp.ClientSession, key: str) -> dict:
    """
    Core resolver:
      1. Android UA → API metadata
      2. Android UA → normal_download page URL
      3. Browser UA → HTML scrape → CDN URL
    Raises Exception on any failure (caller decide kare catch karna hai ya nahi).
    FIX: Ab exception silently nahi swallow hoti.
    """
    # Step 1: File metadata
    data = await _get_json(
        session, f"{FILE_GET_INFO}?quick_key={key}&response_format=json"
    )
    resp_data = data.get("response", {})
    if resp_data.get("result") != "Success":
        raise Exception(f"API error: {resp_data.get('message', 'Unknown')}")
    fi = resp_data.get("file_info", {})

    # Step 2: normal_download page URL
    ldata = await _get_json(
        session,
        f"{FILE_GET_LINKS}?quick_key={key}&link_type=normal_download&response_format=json",
    )
    links = ldata.get("response", {}).get("links", [])
    page_url = links[0].get("normal_download", "") if links else ""
    if not page_url:
        raise Exception("No normal_download link returned by API.")

    # Step 3: CDN URL from HTML (Browser UA — same session, different headers)
    cdn_url = await _extract_cdn_url(session, page_url)

    return {
        "name": fi.get("filename", "file"),
        "size": _parse_size(fi.get("size", "0")),
        "url":  cdn_url,
        "key":  key,
    }


# ── Folder scanner ────────────────────────────────────────────────────────────

async def get_folder_files(folder_key: str) -> list:
    files = []
    async with aiohttp.ClientSession() as session:
        await _collect_files(session, folder_key, files, depth=0)
    return files


async def _collect_files(
    session: aiohttp.ClientSession, folder_key: str, result: list, depth: int = 0
):
    if depth > MAX_FOLDER_DEPTH:
        return

    # Files
    chunk = 1
    while True:
        url = (
            f"{FOLDER_GET_CONTENT}?folder_key={folder_key}"
            f"&content_type=files&chunk_size=100&chunk={chunk}&response_format=json"
        )
        try:
            data = await _get_json(session, url)
        except Exception:
            break

        fc = data.get("response", {}).get("folder_content", {})
        for f in fc.get("files") or []:
            file_key = f.get("quickkey", "")
            if file_key:
                result.append({
                    "name": f.get("filename", "file"),
                    "size": _parse_size(f.get("size", "0")),
                    "key":  file_key,
                })

        if fc.get("more_chunks") == "yes":
            chunk += 1
        else:
            break

    # Sub-folders (recursive)
    sub_chunk = 1
    while True:
        url = (
            f"{FOLDER_GET_CONTENT}?folder_key={folder_key}"
            f"&content_type=folders&chunk_size=100&chunk={sub_chunk}&response_format=json"
        )
        try:
            data = await _get_json(session, url)
        except Exception:
            break

        fc = data.get("response", {}).get("folder_content", {})
        for sf in fc.get("folders") or []:
            sub_key = sf.get("folderkey", "")
            if sub_key:
                await _collect_files(session, sub_key, result, depth=depth + 1)

        if fc.get("more_chunks") == "yes":
            sub_chunk += 1
        else:
            break


# ── Downloader ────────────────────────────────────────────────────────────────

async def download(
    url: str,
    dest: str,
    progress_cb: Optional[Callable] = None,
    cancel_check: Optional[Callable] = None,
    chunk_size: int = CHUNK_SIZE,
):
    """
    CDN URL se actual file stream karo dest mein.
    Android UA — CDN pe koi HTML redirect nahi.
    Content-Type check: HTML aaya toh Exception + retry.
    """
    last_exc = Exception("Download failed after all retries")
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=TIMEOUT_DL,
                    allow_redirects=True,
                    headers=DL_HEADERS,
                ) as resp:
                    resp.raise_for_status()

                    ct = resp.headers.get("Content-Type", "")
                    if "text/html" in ct:
                        raise Exception(
                            "Got HTML instead of file — CDN URL expired. Retrying."
                        )

                    total = int(resp.headers.get("Content-Length", 0))
                    done  = 0
                    async with aiofiles.open(dest, "wb") as f:
                        async for chunk in resp.content.iter_chunked(chunk_size):
                            if cancel_check and cancel_check():
                                raise asyncio.CancelledError("User cancelled")
                            await f.write(chunk)
                            done += len(chunk)
                            if progress_cb:
                                await progress_cb(done, total)
            return  # success

        except asyncio.CancelledError:
            raise
        except Exception as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY * attempt)
                if os.path.exists(dest):
                    try:
                        os.remove(dest)
                    except Exception:
                        pass
    raise last_exc
