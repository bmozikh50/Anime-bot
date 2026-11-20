# media.py — دانلود، تقسیم و ارسال
import glob
import math
import os
import shutil
import subprocess
import sys
import time
import requests
from config import BOT_TOKEN, PART_BYTES, MAX_HEIGHT, UA, TG

CHUNK = 1024 * 256


def _probe_duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=120).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def _m3u8_size(url, referer):
    """حجم تقریبی m3u8 از BANDWIDTH (فقط برای پیش‌بینی زمان)."""
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Referer": referer}, timeout=30)
        bw = max([int(x) for x in __import__("re").findall(r"BANDWIDTH=(\d+)", r.text)] or [0])
        dur = sum(float(x) for x in __import__("re").findall(r"#EXTINF:([\d.]+)", r.text))
        return (bw / 8) * dur
    except Exception:
        return 0


def download(url, referer, out_path, progress=None):
    """دانلود با yt-dlp برای HLS و requests برای mp4."""
    if ".m3u8" in url:
        cmd = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-playlist",
               "-N", "6", "--retries", "10", "--fragment-retries", "10",
               "--referer", referer, "--add-header", f"Origin: {referer.split('/stream')[0]}",
               "-f", f"bv*[height<={MAX_HEIGHT}]+ba/b[height<={MAX_HEIGHT}]/b",
               "--merge-output-format", "mp4", "-o", out_path, url]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        last = 0.0
        for line in proc.stdout:
            if progress and time.time() - last > 4:
                last = time.time()
                m = __import__("re").search(r"(\d{1,3}\.\d)%", line)
                if m:
                    progress(float(m.group(1)))
        if proc.wait() != 0 or not os.path.exists(out_path):
            raise RuntimeError("yt-dlp ناموفق بود")
        return out_path

    with requests.get(url, headers={"User-Agent": UA, "Referer": referer},
                      stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        last = 0.0
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(CHUNK):
                f.write(chunk)
                done += len(chunk)
                if progress and total and time.time() - last > 4:
                    last = time.time()
                    progress(done * 100.0 / total)
    return out_path


def split_parts(path, workdir):
    """تقسیم فایل به پارچه‌های کمتر از سقف تلگرام."""
    size = os.path.getsize(path)
    if size <= PART_BYTES:
        return [path]
    dur = _probe_duration(path) or 0
    count = math.ceil(size / PART_BYTES)
    seg = max(20, int(dur / count) + 1) if dur else 60
    out_dir = os.path.join(workdir, "parts")
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-c", "copy",
                    "-map", "0", "-f", "segment", "-segment_time", str(seg),
                    "-reset_timestamps", "1", "-movflags", "+faststart",
                    os.path.join(out_dir, "p_%03d.mp4")],
                   check=True, timeout=1800)
    parts = sorted(glob.glob(os.path.join(out_dir, "p_*.mp4")))
    final = []
    for p in parts:
        if os.path.getsize(p) <= 49 * 1024 * 1024:
            final.append(p)
            continue
        sub = split_parts(p, os.path.join(out_dir, os.path.basename(p)))
        final.extend(sub)
    return final


def _post(method, data, files=None):
    return requests.post(f"{TG}/{method}", data=data, files=files, timeout=900)


def send_parts(chat_id, parts, caption, progress=None):
    total = len(parts)
    for i, p in enumerate(parts, 1):
        cap = f"{caption}\n\nپارت {i} از {total}" if total > 1 else caption
        ok = False
        with open(p, "rb") as fh:
            r = _post("sendVideo", {"chat_id": chat_id, "caption": cap,
                                    "supports_streaming": "true",
                                    "parse_mode": "HTML"},
                      {"video": (os.path.basename(p), fh, "video/mp4")})
        if r.ok:
            ok = True
        else:
            with open(p, "rb") as fh:
                r2 = _post("sendDocument", {"chat_id": chat_id, "caption": cap,
                                            "parse_mode": "HTML"},
                           {"document": (os.path.basename(p), fh, "video/mp4")})
            ok = r2.ok
        if not ok:
            raise RuntimeError(f"آپلود پارت {i} شکست خورد: {r.text[:150]}")
        if progress:
            progress(i * 100.0 / total)
        time.sleep(2)
