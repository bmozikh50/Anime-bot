# anime.py — جستجو و استخراج لینک از منابع
import html as _h
import re
import requests
from config import UA, HTTP_TIMEOUT

S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})

ANILIST = "https://graphql.anilist.co"
Q_SEARCH = """query($s:String){Page(perPage:8){media(search:$s,type:ANIME,
sort:SEARCH_MATCH){id episodes format seasonYear title{romaji english}
coverImage{medium} nextAiringEpisode{episode}}}}"""

WCO = {"sub": "https://www.wcoanimesub.tv", "dub": "https://www.wcoanimesub.tv"}
WCO["dub"] = "https://www.wcoanimedub.tv"

MEGAPLAY = "https://megaplay.buzz"


def _clean(t):
    return re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", "", t))).strip()


# ---------- جستجو ----------
def anilist_search(q):
    r = S.post(ANILIST, json={"query": Q_SEARCH, "variables": {"s": q}},
               timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    out = []
    for m in r.json()["data"]["Page"]["media"]:
        title = m["title"].get("english") or m["title"].get("romaji")
        if not title:
            continue
        out.append({
            "id": m["id"], "title": title, "eps": m.get("episodes"),
            "next": (m.get("nextAiringEpisode") or {}).get("episode"),
            "year": m.get("seasonYear"), "format": m.get("format"),
        })
    return out


def episode_total(item):
    if item.get("eps"):
        return int(item["eps"])
    if item.get("next"):
        return max(1, int(item["next"]) - 1)
    return 1


# ---------- منبع ۱: MegaPlay (کتابخانه انیمه‌کوتو / HiAnime) ----------
def mp_embed(anilist_id, ep, lang):
    return f"{MEGAPLAY}/stream/ani/{anilist_id}/{ep}/{lang}"


def _m3u8_in(text):
    m = re.search(r'https?://[^"\'\s\\<>]+\.m3u8[^"\'\s\\<>]*', text)
    return m.group(0) if m else None


def resolve_megaplay(anilist_id, ep, lang):
    """(url, referer) برمی‌گرداند؛ در صورت شکست None."""
    embed = mp_embed(anilist_id, ep, lang)
    try:
        r = S.get(embed, headers={"Referer": MEGAPLAY + "/"}, timeout=HTTP_TIMEOUT)
    except requests.RequestException:
        return None
    if r.status_code >= 400:
        return None
    page = r.text

    direct = _m3u8_in(page)
    if direct:
        return direct, embed

    ids = []
    for pat in (r'data-id="([^"]+)"', r'data-pjax="([^"]+)"',
                r'id="player"[^>]*data-id="([^"]+)"', r'/e-\d+/([A-Za-z0-9]{6,})'):
        ids += re.findall(pat, page)
    for pid in dict.fromkeys(ids):
        for cand in (f"{MEGAPLAY}/stream/getSources?id={pid}",
                     f"{MEGAPLAY}/getSources?id={pid}",
                     f"{MEGAPLAY}/ajax/getSources?id={pid}",
                     f"https://megacloud.tv/embed-2/ajax/e-1/getSources?id={pid}"):
            try:
                rr = S.get(cand, headers={"Referer": embed,
                                          "X-Requested-With": "XMLHttpRequest"},
                           timeout=HTTP_TIMEOUT)
            except requests.RequestException:
                continue
            found = _m3u8_in(rr.text)
            if found:
                return found, embed
    return None


# ---------- منبع ۲: WCO (ساب / داب) ----------
def wco_search(q, lang):
    base = WCO.get(lang, WCO["sub"])
    for param in ("q", "search", "s", "keyword"):
        try:
            r = S.get(base + "/search", params={param: q},
                      headers={"Referer": base + "/"}, timeout=HTTP_TIMEOUT)
        except requests.RequestException:
            continue
        links, seen = [], set()
        for href, text in re.findall(r']+href="([^"]+)"[^>]*>(.*?)',
                                     r.text, re.S):
            if "episode-" in href or not text.strip():
                continue
            if "english-subbed" not in href and "english-dubbed" not in href:
                continue
            full = href if href.startswith("http") else base + href
            if full in seen:
                continue
            seen.add(full)
            links.append({"url": full, "title": _clean(text)})
            if len(links) >= 8:
                break
        if links:
            return links
    return []


def wco_episodes(series_url):
    try:
        r = S.get(series_url, timeout=HTTP_TIMEOUT)
    except requests.RequestException:
        return []
    eps, seen = [], set()
    for href, num in re.findall(r'href="([^"]*?episode-(\d+)[^"]*)"', r.text):
        n = int(num)
        if n in seen:
            continue
        seen.add(n)
        full = href if href.startswith("http") else series_url.split("/anime/")[0] + href
        eps.append({"ep": n, "url": full})
    return sorted(eps, key=lambda x: x["ep"])


def resolve_wco(episode_url):
    """صفحه قسمت → iframe → فایل mp4 مستقیم."""
    try:
        page = S.get(episode_url, timeout=HTTP_TIMEOUT).text
    except requests.RequestException:
        return None
    frames = re.findall(r']+src="([^"]+)"', page)
    for f in frames:
        if f.startswith("//"):
            f = "https:" + f
        elif f.startswith("/"):
            f = "https://www.wcoanimesub.tv" + f
        try:
            inner = S.get(f, headers={"Referer": episode_url}, timeout=HTTP_TIMEOUT).text
        except requests.RequestException:
            continue
        for pat in (r'"file"\s*:\s*"([^"]+\.mp4[^"]*)"',
                    r'sources\s*:\s*\[\s*\{[^}]*?file\s*:\s*"([^"]+)"',
                    r'https?://[^"\'\s]+\.mp4[^"\'\s]*'):
            m = re.search(pat, inner)
            if m:
                return m.group(1) if m.groups() else m.group(0), f
        for pat in (r'https?://[^"\'\s]+\.m3u8[^"\'\s]*',):
            m = re.search(pat, inner)
            if m:
                return m.group(0), f
    return None


# ---------- مسیریاب یکپارچه ----------
def resolve(source, item, ep, lang):
    if source == "mp":
        return resolve_megaplay(item["id"], ep, lang)
    if source == "wco":
        eps = wco_episodes(item["url"])
        match = next((e for e in eps if e["ep"] == ep), None)
        if not match:
            return None
        return resolve_wco(match["url"])
    return None
