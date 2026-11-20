# config.py — تنظیمات
import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_ID  = int(os.environ.get("OWNER_ID", "0") or 0)

_allow = os.environ.get("ALLOWED_USERS", "").replace(" ", "")
ALLOWED = {int(x) for x in _allow.split(",") if x}

WORK_DIR   = os.environ.get("WORK_DIR", "/tmp/anime")
PART_BYTES = 44 * 1024 * 1024      # اندازه هر پارچه (سقف تلگرام 50MB)
MAX_HEIGHT = int(os.environ.get("MAX_HEIGHT", "720"))   # سقف کیفیت
MAX_RUNTIME = int(os.environ.get("MAX_RUNTIME", "19800"))  # ثانیه
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HTTP_TIMEOUT = 40
TG = f"https://api.telegram.org/bot{BOT_TOKEN}"
