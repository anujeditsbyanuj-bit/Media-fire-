"""
handlers/downloader.py — Core download + upload logic
"""

import os
import time
import asyncio
import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message

import config
import database as db
import mediafire_dl as mf
from utils import human_size, progress_bar, eta_str, is_mediafire_link, split_file, logger

cancel_flags: dict = {}

_semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_DOWNLOADS)

PROGRESS_INTERVAL = 3
TG_MAX_BYTES      = 2 * 1024 ** 3   # 2 GB


def register_downloader(app: Client):

    @app.on_message(filters.text & filters.private & ~filters.command(
        ["start", "help", "profile", "history", "ping", "cancel",
         "premium", "redeem", "stats", "genkey", "listkeys", "delkey",
         "addpremium", "revokepremium", "broadcast", "ban", "unban", "users"]
    ), group=1)
    async def on_link(c: Client, m: Message):
        if not m.text:
            return

        url = m.text.strip()
        if not is_mediafire_link(url):
            return

        user = m.from_user
        if not user:
            return

        try:
            u = await db.get_user(
                user.id,
                ((user.first_name or "") + (" " + user.last_name if user.last_name else "")).strip()
            )
        except Exception as e:
            logger.error(f"DB error in on_link: {e}")
            await m.reply("❌ Database error. Please try again.")
            return

        if u.get("is_banned", False):
            await m.reply("🚫 You are banned.")
            return

        if not u.get("is_premium", False) and u.get("downloads_today", 0) >= config.FREE_DAILY_LIMIT:
            await m.reply(
                f"⚠️ **Daily limit reached** ({config.FREE_DAILY_LIMIT}/day for free users).\n\n"
                f"👑 Use /premium to upgrade!"
            )
            return

        if user.id in cancel_flags and cancel_flags[user.id] is False:
            await m.reply("⏳ You already have an active download. Use /cancel to stop it.")
            return

        status = await m.reply("🔍 Resolving link…")
        cancel_flags[user.id] = False

        if mf.is_folder_link(url):
            try:
                await _handle_folder(c, m, url, u, status)
            except Exception as e:
                logger.exception(f"Unhandled error in folder handler: {e}")
                try:
                    await status.edit(f"❌ Unexpected error: `{e}`")
                except Exception:
                    pass
            finally:
                cancel_flags.pop(user.id, None)
        else:
            async with _semaphore:
                try:
                    await _handle_file(c, m, url, u, status)
                except Exception as e:
                    logger.exception(f"Unhandled error in download handler: {e}")
                    try:
                        await status.edit(f"❌ Unexpected error: `{e}`")
                    except Exception:
                        pass
                finally:
                    cancel_flags.pop(user.id, None)


def _sanitize(name: str, fallback: str = "file") -> str:
    name = "".join(ch for ch in name if ch not in r'\/:*?"<>|' and ord(ch) >= 32)
    name = name.replace("..", ".")
    return name.strip(". ") or fallback


async def _handle_file(c, m, url, u, status):
    uid = m.from_user.id

    # FIX: get_info() ab exception raise karta hai — actual error user ko dikhega
    try:
        info = await mf.get_info(url)
    except Exception as e:
        logger.warning(f"Resolve failed for {url}: {e}")
        await status.edit(f"❌ Could not resolve link.\n`{e}`")
        return

    if not info or not info.get("url"):
        await status.edit("❌ No download link found. File may be private or deleted.")
        return

    size_bytes = info.get("size") or 0
    size_mb    = size_bytes / (1024 * 1024) if size_bytes else 0
    max_mb     = config.PREMIUM_MAX_SIZE_MB if u.get("is_premium", False) else config.FREE_MAX_SIZE_MB

    if size_mb and size_mb > max_mb:
        note = "\n👑 Use /premium to upgrade to 4 GB limit." if not u.get("is_premium", False) else ""
        await status.edit(
            f"❌ File too large: **{human_size(size_bytes)}**\n"
            f"Your plan allows max **{max_mb} MB**.{note}"
        )
        return

    filename = _sanitize(info.get("name") or "file")
    dest     = os.path.join(config.DOWNLOAD_DIR, f"{uid}_{filename}")
    os.makedirs(config.DOWNLOAD_DIR, exist_ok=True)

    size_str = human_size(size_bytes) if size_bytes else "Unknown size"
    await status.edit(
        f"📥 **Starting download…**\n`{filename}`\n"
        f"📦 Size: **{size_str}**"
    )

    start_time = time.time()
    last_edit  = [0.0]

    async def dl_progress(done: int, total: int):
        now = time.time()
        if now - last_edit[0] < PROGRESS_INTERVAL:
            return
        last_edit[0] = now
        elapsed = now - start_time
        speed   = done / elapsed if elapsed else 0
        pct     = done / total * 100 if total else 0
        eta     = int((total - done) / speed) if speed and total else 0
        try:
            await status.edit(
                f"📥 **Downloading…**\n\n"
                f"`{filename}`\n\n"
                f"{progress_bar(pct)} **{pct:.1f}%**\n\n"
                f"⬇️ {human_size(done)} / {human_size(total)}\n"
                f"⚡ {human_size(int(speed))}/s\n"
                f"⏱ ETA: {eta_str(eta)}"
            )
        except Exception:
            pass

    try:
        await mf.download(
            info["url"], dest,
            progress_cb  = dl_progress,
            cancel_check = lambda: cancel_flags.get(uid, False),
        )
    except asyncio.CancelledError:
        await status.edit("🚫 Download cancelled.")
        _rm(dest)
        return
    except Exception as e:
        logger.exception(f"Download failed: {url}")
        await status.edit(f"❌ Download failed.\n`{e}`")
        _rm(dest)
        return

    if not os.path.exists(dest) or os.path.getsize(dest) == 0:
        await status.edit("❌ Downloaded file is empty. Please try again.")
        _rm(dest)
        return

    file_size = os.path.getsize(dest)
    actual_mb = file_size / (1024 * 1024)
    if actual_mb > max_mb:
        note = "\n👑 Upgrade to Premium for 4 GB limit." if not u.get("is_premium", False) else ""
        await status.edit(
            f"❌ Downloaded file too large: **{human_size(file_size)}**\n"
            f"Your plan allows max **{max_mb} MB**.{note}"
        )
        _rm(dest)
        return

    success = await _upload(c, m, dest, filename, file_size, status, uid)
    if success:
        await db.add_history(uid, filename, human_size(file_size))
    _rm(dest)


async def _upload(c, m, path, name, size, status, uid, delete_status_on_success: bool = True) -> bool:
    """Returns True on success, False on failure."""
    start_time = time.time()
    last_edit  = [0.0]

    async def up_progress(current: int, total: int):
        now = time.time()
        if now - last_edit[0] < PROGRESS_INTERVAL:
            return
        last_edit[0] = now
        elapsed = now - start_time
        speed   = current / elapsed if elapsed else 0
        pct     = current / total * 100 if total else 0
        eta     = int((total - current) / speed) if speed and total else 0
        try:
            await status.edit(
                f"📤 **Uploading…**\n\n"
                f"`{name}`\n\n"
                f"{progress_bar(pct)} **{pct:.1f}%**\n\n"
                f"⬆️ {human_size(current)} / {human_size(total)}\n"
                f"⚡ {human_size(int(speed))}/s\n"
                f"⏱ ETA: {eta_str(eta)}"
            )
        except Exception:
            pass

    if size <= TG_MAX_BYTES:
        try:
            await status.edit(f"📤 **Uploading to Telegram…**\n`{name}`")
        except Exception:
            pass
        try:
            await c.send_document(
                chat_id   = m.chat.id,
                document  = path,
                file_name = name,
                caption   = f"✅ **{name}**\n📦 {human_size(size)}",
                progress  = up_progress,
            )
            if delete_status_on_success:
                await status.delete()
            else:
                try:
                    await status.edit(f"✅ Uploaded: `{name}`")
                except Exception:
                    pass
            return True
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            await status.edit(f"❌ Upload failed.\n`{e}`")
            return False

    # File > 2 GB — split into parts
    await status.edit("✂️ File > 2 GB — splitting into parts…")
    parts  = split_file(path, config.SPLIT_SIZE_BYTES)
    all_ok = True
    for i, part in enumerate(parts, 1):
        pname = os.path.basename(part)
        psize = os.path.getsize(part)
        try:
            await status.edit(f"📤 Uploading part {i}/{len(parts)}: `{pname}`")
            await c.send_document(
                chat_id   = m.chat.id,
                document  = part,
                file_name = pname,
                caption   = f"📦 Part {i}/{len(parts)} — {human_size(psize)}",
                progress  = up_progress,
            )
        except Exception as e:
            logger.error(f"Part {i} upload failed: {e}")
            try:
                await status.edit(f"❌ Part {i} failed.\n`{e}`")
            except Exception:
                pass
            all_ok = False
        finally:
            _rm(part)

    if all_ok:
        await m.reply(
            f"✅ All {len(parts)} parts uploaded!\n\n"
            f"**Linux/Mac:**\n`cat {name}.part* > {name}`\n\n"
            f"**Windows CMD:**\n`copy /b {name}.part* {name}`"
        )
        if delete_status_on_success:
            try:
                await status.delete()
            except Exception:
                pass

    return all_ok


async def _handle_folder(c, m, url, u, status):
    """
    Folder ki saari files download karke Telegram pe upload karo.
    Har file ke liye CDN URL resolve hota hai (HTML scrape via Browser UA).
    Individual file errors skip hote hain — loop continue karta hai.
    """
    if not u.get("is_premium", False):
        await status.edit(
            "📁 **Folder downloads are Premium only.**\n\n"
            "👑 Use /premium to upgrade."
        )
        return

    folder_key = mf.extract_folder_key(url)
    if not folder_key:
        await status.edit("❌ Could not extract folder key from URL.")
        return

    await status.edit("🔍 Scanning folder…")
    try:
        files = await mf.get_folder_files(folder_key)
    except Exception as e:
        await status.edit(f"❌ Folder scan failed.\n`{e}`")
        return

    if not files:
        await status.edit("📭 Folder is empty or private.")
        return

    uid         = m.from_user.id
    max_mb      = config.PREMIUM_MAX_SIZE_MB
    total_files = len(files)
    total_size  = sum(f.get("size", 0) for f in files)

    os.makedirs(config.DOWNLOAD_DIR, exist_ok=True)

    await status.edit(
        f"📁 Found **{total_files} files** ({human_size(total_size)})\n"
        f"⬇️ Starting downloads…"
    )

    success_count = 0
    skip_count    = 0
    fail_count    = 0

    async with aiohttp.ClientSession() as resolve_session:

        for idx, finfo in enumerate(files, 1):

            if cancel_flags.get(uid):
                await status.edit(f"🚫 Cancelled after {idx - 1}/{total_files} files.")
                return

            fname    = finfo.get("name", "file")
            file_key = finfo.get("key", "")

            try:
                await status.edit(
                    f"📥 **File {idx}/{total_files}**\n"
                    f"`{fname}`\n\n"
                    f"✅ {success_count} done  "
                    f"⚠️ {skip_count} skipped  "
                    f"❌ {fail_count} failed"
                )
            except Exception:
                pass

            if not file_key:
                await m.reply(f"⚠️ [{idx}/{total_files}] Skipped (no key): `{fname}`")
                skip_count += 1
                continue

            # Step 1: shared session se resolve (Android UA + Browser UA)
            # FIX: get_file_info_by_key None return karta hai on failure (folder loop break na ho)
            info = await mf.get_file_info_by_key(resolve_session, file_key)

            # Step 2: Fallback — naya session se try karo
            if not info or not info.get("url"):
                try:
                    info = await mf.get_info(
                        f"https://www.mediafire.com/file/{file_key}"
                    )
                except Exception as e:
                    logger.warning(f"[{idx}/{total_files}] Resolve failed for {fname}: {e}")
                    await m.reply(f"⚠️ [{idx}/{total_files}] Could not resolve `{fname}`: `{e}`")
                    fail_count += 1
                    continue

            if not info or not info.get("url"):
                await m.reply(f"⚠️ [{idx}/{total_files}] No download link: `{fname}`")
                skip_count += 1
                continue

            # Size check
            f_size_bytes = info.get("size") or 0
            f_size_mb    = f_size_bytes / (1024 * 1024) if f_size_bytes else 0

            if f_size_mb and f_size_mb > max_mb:
                await m.reply(
                    f"⚠️ [{idx}/{total_files}] Skipped `{fname}` — "
                    f"too large: **{human_size(f_size_bytes)}** (max {max_mb} MB)"
                )
                skip_count += 1
                continue

            safe_name = _sanitize(info.get("name") or fname)
            dest      = os.path.join(config.DOWNLOAD_DIR, f"{uid}_{idx}_{safe_name}")

            # Download
            try:
                await mf.download(
                    info["url"], dest,
                    cancel_check=lambda u=uid: cancel_flags.get(u, False),
                )
            except asyncio.CancelledError:
                await status.edit(f"🚫 Cancelled at file {idx}/{total_files}.")
                _rm(dest)
                return
            except Exception as e:
                logger.warning(f"[{idx}/{total_files}] Download failed {fname}: {e}")
                await m.reply(f"❌ [{idx}/{total_files}] Download failed `{fname}`: `{e}`")
                _rm(dest)
                fail_count += 1
                continue

            # Verify
            if not os.path.exists(dest) or os.path.getsize(dest) == 0:
                await m.reply(f"⚠️ [{idx}/{total_files}] Empty file, skipped: `{fname}`")
                _rm(dest)
                skip_count += 1
                continue

            fsize     = os.path.getsize(dest)
            actual_mb = fsize / (1024 * 1024)

            if actual_mb > max_mb:
                await m.reply(
                    f"⚠️ [{idx}/{total_files}] Skipped `{fname}` — "
                    f"actual size {human_size(fsize)} exceeds {max_mb} MB limit."
                )
                _rm(dest)
                skip_count += 1
                continue

            # Upload
            ok = await _upload(c, m, dest, safe_name, fsize, status, uid, delete_status_on_success=False)
            if ok:
                await db.add_history(uid, safe_name, human_size(fsize))
                success_count += 1
                await asyncio.sleep(1)  # Telegram flood limit avoid karo
            else:
                fail_count += 1

            _rm(dest)

    # Final summary
    try:
        await status.delete()
    except Exception:
        pass
    await m.reply(
        f"📁 **Folder complete!**\n\n"
        f"✅ Uploaded: **{success_count}/{total_files}**\n"
        f"⚠️ Skipped: **{skip_count}**\n"
        f"❌ Failed: **{fail_count}**"
    )


def _rm(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass
