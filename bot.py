# bot.py — هسته ربات
import os
import queue
import re
import shutil
import signal
import threading
import time
import traceback
import requests

from config import (BOT_TOKEN, OWNER_ID, ALLOWED, WORK_DIR, MAX_RUNTIME, TG)
import anime
import media

PER_PAGE = 12
SESS = {}
JOBS = queue.Queue()
START = time.time()
STOP = threading.Event()
BUSY = threading.Lock()


def tg(method, **data):
    try:
        return requests.post(f"{TG}/{method}", data=data, timeout=60).json()
    except requests.RequestException:
        return {}


def say(chat, text, kb=None):
    d = {"chat_id": chat, "text": text, "parse_mode": "HTML",
         "disable_web_page_preview": "true"}
    if kb:
        import json
        d["reply_markup"] = json.dumps(kb)
    return tg("sendMessage", **d)


def edit(chat, msg_id, text, kb=None):
    import json
    d = {"chat_id": chat, "message_id": msg_id, "text": text, "parse_mode": "HTML"}
    if kb:
        d["reply_markup"] = json.dumps(kb)
    tg("editMessageText", **d)


def allowed(uid):
    if ALLOWED:
        return uid in ALLOWED or uid == OWNER_ID
    return True


def kb_rows(rows):
    return {"inline_keyboard": rows}


# ---------------- نمایش‌ها ----------------
def show_results(chat, query):
    try:
        items = anime.anilist_search(query)
    except Exception:
        say(chat, "جستجو ناموفق بود. کمی بعد دوباره تلاش کن.")
        return
    if not items:
        say(chat, "چیزی پیدا نشد. املای نام یا نام انگلیسی/ژاپنی را امتحان کن.")
        return
    SESS[chat] = {"items": {str(i["id"]): i for i in items}}
    rows = []
    for it in items:
        extra = f" ({it['year']})" if it.get("year") else ""
        rows.append([{"text": f"{it['title'][:52]}{extra}",
                      "callback_data": f"m:{it['id']}"}])
    say(chat, "نتیجه‌ای که می‌خواهی را انتخاب کن:", kb_rows(rows))


def show_sources(chat, msg_id, anilist_id):
    s = SESS.setdefault(chat, {})
    it = s.get("items", {}).get(str(anilist_id))
    if not it:
        edit(chat, msg_id, "نشست منقضی شد. دوباره جستجو کن.")
        return
    s["item"] = it
    s["wco_url"] = None
    rows = [
        [{"text": "🎬 انیمه‌کوتو (ساب)", "callback_data": "s:mp:sub"},
         {"text": "🎬 انیمه‌کوتو (داب)", "callback_data": "s:mp:dub"}],
        [{"text": "📼 WCO (ساب)", "callback_data": "s:wco:sub"},
         {"text": "📼 WCO (داب)", "callback_data": "s:wco:dub"}],
    ]
    edit(chat, msg_id,
         f"{it['title']}\n\nمنبع و نوع صدا را انتخاب کن.\n"
         f"داب همیشه موجود نیست؛ اگر منبع جواب نداد ساب را امتحان کن.",
         kb_rows(rows))


def build_episodes(chat, src, lang):
    s = SESS.get(chat) or {}
    it = s.get("item")
    if not it:
        return None, "نشست منقضی شد."
    if src == "mp":
        total = anime.episode_total(it)
        s["eps"] = list(range(1, total + 1))
    else:
        res = anime.wco_search(it["title"], lang)
        if not res:
            return None, "این عنوان در WCO پیدا نشد. منبع دیگر را امتحان کن."
        best = res[0]
        s["wco_url"] = best["url"]
        lst = anime.wco_episodes(best["url"])
        if not lst:
            return None, "قسمتی در WCO پیدا نشد."
        s["eps"] = [e["ep"] for e in lst]
        s["item"]["url"] = best["url"]
    s["src"], s["lang"], s["page"] = src, lang, 0
    return s["eps"], None


def show_episodes(chat, msg_id, page):
    s = SESS.get(chat) or {}
    eps = s.get("eps") or []
    if not eps:
        edit(chat, msg_id, "لیست قسمت خالی است.")
        return
    pages = max(1, (len(eps) + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, pages - 1))
    s["page"] = page
    chunk = eps[page * PER_PAGE:(page + 1) * PER_PAGE]
    rows, row = [], []
    for e in chunk:
        row.append({"text": str(e), "callback_data": f"e:{e}"})
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav = []
    if page > 0:
        nav.append({"text": "◀️", "callback_data": f"pg:{page - 1}"})
    nav.append({"text": f"{page + 1}/{pages}", "callback_data": "noop"})
    if page < pages - 1:
        nav.append({"text": "▶️", "callback_data": f"pg:{page + 1}"})
    rows.append(nav)
    rows.append([{"text": "🔙 تغییر منبع", "callback_data": "bk"}])
    tag = "ساب" if s.get("lang") == "sub" else "داب"
    edit(chat, msg_id,
         f"{s['item']['title']}\nمنبع: {s.get('src')} | {tag}\n\n"
         f"قسمت را انتخاب کن ({len(eps)} قسمت):", kb_rows(rows))


# ---------------- کارگر دانلود ----------------
def worker():
    while not STOP.is_set():
        try:
            job = JOBS.get(timeout=1)
        except queue.Empty:
            continue
        chat, item, src, lang, ep = job
        status = say(chat, "⏳ در حال آماده‌سازی…")
        msg_id = (status.get("result") or {}).get("message_id")
        work = os.path.join(WORK_DIR, str(chat))
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work, exist_ok=True)
        try:
            got = anime.resolve(src, item, ep, lang)
            if not got:
                edit(chat, msg_id, "❌ لینک این قسمت به دست نیامد. "
                                   "منبع یا نوع صدا (ساب/داب) را عوض کن.")
                continue
            url, referer = got
            edit(chat, msg_id, "⬇️ در حال دانلود…")
            out = os.path.join(work, "v.mp4")
            last = [0.0]

            def prog(p):
                if time.time() - last[0] > 4:
                    last[0] = time.time()
                    edit(chat, msg_id, f"⬇️ دانلود: {p:.0f}%")

            media.download(url, referer, out, prog)
            size = os.path.getsize(out) / (1024 * 1024)
            edit(chat, msg_id, f"✂️ تقسیم فایل ({size:.0f} مگابایت)…")
            parts = media.split_parts(out, work)
            cap = (f"{item['title']}\nقسمت {ep} | "
                   f"{'ساب' if lang == 'sub' else 'داب'}")
            edit(chat, msg_id, f"📤 ارسال {len(parts)} پارت…")
            media.send_parts(chat, parts, cap)
            edit(chat, msg_id, f"✅ تمام شد ({len(parts)} پارت).")
        except Exception as exc:
            traceback.print_exc()
            edit(chat, msg_id, f"❌ خطا: {str(exc)[:200]}\n"
                               "منبع دیگری را امتحان کن.")
        finally:
            shutil.rmtree(work, ignore_errors=True)


# ---------------- رویدادها ----------------
def on_message(m):
    chat = m["chat"]["id"]
    uid = m["from"]["id"]
    if not allowed(uid):
        say(chat, "دسترسی نداری.")
        return
    text = (m.get("text") or "").strip()
    if text.startswith("/"):
        if text.startswith("/start") or text.startswith("/help"):
            say(chat, "ربات دانلود انیمه\n\n"
                      "۱) نام انیمه را انگلیسی بفرست.\n"
                      "۲) از لیست انتخاب کن.\n"
                      "۳) منبع و ساب/داب را بزن.\n"
                      "۴) قسمت را بزن تا دانلود و ارسال شود.\n\n"
                      "فایل‌های بزرگ به پارت‌های ۴۴ مگابایتی تقسیم می‌شوند.")
        elif text.startswith("/cancel"):
            JOBS.queue.clear()
            say(chat, "صف پاک شد.")
        return
    show_results(chat, text)


def on_callback(c):
    chat = c["message"]["chat"]["id"]
    msg_id = c["message"]["message_id"]
    data = c.get("data") or ""
    tg("answerCallbackQuery", callback_query_id=c["id"])
    if not allowed(c["from"]["id"]):
        return
    try:
        if data.startswith("m:"):
            show_sources(chat, msg_id, data.split(":", 1)[1])
        elif data.startswith("s:"):
            _, src, lang = data.split(":")
            eps, err = build_episodes(chat, src, lang)
            if err:
                edit(chat, msg_id, f"❌ {err}")
                return
            show_episodes(chat, msg_id, 0)
        elif data.startswith("pg:"):
            show_episodes(chat, msg_id, int(data.split(":")[1]))
        elif data == "bk":
            s = SESS.get(chat) or {}
            show_sources(chat, msg_id, str(s.get("item", {}).get("id")))
        elif data.startswith("e:"):
            s = SESS.get(chat) or {}
            ep = int(data.split(":")[1])
            if not s.get("item"):
                edit(chat, msg_id, "نشست منقضی شد.")
                return
            if BUSY.locked() or not JOBS.empty():
                say(chat, f"🕒 قسمت {ep} به صف اضافه شد.")
            JOBS.put((chat, dict(s["item"]), s["src"], s["lang"], ep))
            tg("answerCallbackQuery", callback_query_id=c["id"],
               text=f"قسمت {ep} در صف")
        elif data != "noop":
            pass
    except Exception:
        traceback.print_exc()
        edit(chat, msg_id, "خطای غیرمنتظره. دوباره تلاش کن.")


def poll_loop():
    offset = None
    while not STOP.is_set():
        try:
            r = requests.get(f"{TG}/getUpdates", timeout=70, params={
                "timeout": 50, "offset": offset,
                "allowed_updates": '["message","callback_query"]'}).json()
        except requests.RequestException:
            time.sleep(3)
            continue
        for u in r.get("result", []):
            offset = u["update_id"] + 1
            try:
                if "message" in u:
                    on_message(u["message"])
                elif "callback_query" in u:
                    on_callback(u["callback_query"])
            except Exception:
                traceback.print_exc()


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN تنظیم نشده است")
    os.makedirs(WORK_DIR, exist_ok=True)
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    os.makedirs(WORK_DIR, exist_ok=True)
    tg("setMyCommands", commands='[{"command":"start","description":"شروع"},'
                                 '{"command":"cancel","description":"پاک کردن صف"}]')
    threading.Thread(target=worker, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    print("bot up", flush=True)
    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()
    while not STOP.is_set():
        if time.time() - START > MAX_RUNTIME:
            print("runtime limit, exiting", flush=True)
            STOP.set()
        time.sleep(2)
    time.sleep(3)


if __name__ == "__main__":
    main()
