"""새로움 공급원 데이터 수집 → data.json"""
import html
import os
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130 Safari/537.36"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def text(s):
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).replace("　", " ").strip()


def guess_year(month):
    # 연도가 없는 날짜: 지금보다 3달 넘게 이전이면 내년으로 본다
    y = NOW.year
    if month < NOW.month - 3:
        y += 1
    return y


# ---------- 시네마테크 KOFA ----------
def kofa():
    page = get("https://www.koreafilm.or.kr/cinematheque/schedule")
    out = []
    months = re.split(r'<dt class="txt-month">', page)[1:]
    for mblock in months:
        month = int(re.match(r"(\d+)월", mblock).group(1))
        for dblock in re.split(r'<dt class="txt-day">', mblock)[1:]:
            day = int(re.match(r"(\d+)\.", dblock).group(1))
            date = f"{guess_year(month)}-{month:02d}-{day:02d}"
            for item in re.split(r'<ul class="list-detail-1">', dblock)[1:]:
                tm = re.search(r'icon-dot">\s*([\d:]+)', item)
                title = re.search(r'<p class="txt-1"><a href="([^"]+)">([^<]+)</a>', item)
                if not (tm and title):
                    continue

                def field(cls):
                    m = re.search(r'<span class="' + cls + r'"><strong class="hidden">[^<]*</strong>([^<]*)</span>', item)
                    return m.group(1).strip() if m else ""

                room = re.search(r'txt-room">([^<]*)<', item)
                prog = re.search(r'layer-txt-1">([^<]*)<', item)
                out.append({
                    "date": date,
                    "time": tm.group(1),
                    "room": room.group(1).strip() if room else "",
                    "title": html.unescape(title.group(2).strip()),
                    "url": "https://www.koreafilm.or.kr" + title.group(1),
                    "director": html.unescape(field("name")),
                    "year": field("yy"),
                    "runtime": field("min"),
                    "program": html.unescape(prog.group(1).strip()) if prog else "",
                })
    return out


# ---------- 서울대 음대 공연 ----------
def parse_snu_date(s):
    m = re.search(r"(20\d\d)\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.search(r"(\d{1,2})월\s*(\d{1,2})일", s)
        if not m:
            return None
        mo, d = map(int, m.groups())
        y = guess_year(mo)
    return f"{y}-{mo:02d}-{d:02d}"


def snu():
    page = get("https://music.snu.ac.kr/event_in")
    events, lineup_url = [], None
    for slide in page.split('class="swiper-slide swiper-slide-ga"')[1:]:
        link = re.search(r'href="(https://music\.snu\.ac\.kr/event_in/\d+)" class="titles', slide)
        title = re.search(r'class="titles cut">(.*?)</a>', slide, re.S)
        when = re.search(r'main_t_sub2[^>]*>(.*?)</li>', slide, re.S)
        place = re.search(r'main_t_sub3[^>]*>(.*?)</li>', slide, re.S)
        img = re.search(r'<img src="([^"]+)"', slide)
        if not (link and title):
            continue
        t = text(title.group(1))
        w = text(when.group(1)) if when else ""
        if "라인업" in t:
            lineup_url = link.group(1)
            continue
        date = parse_snu_date(w)
        if " ~ " in w or not date:
            continue
        events.append({
            "date": date, "when": w, "title": t,
            "place": text(place.group(1)) if place else "",
            "url": link.group(1), "img": img.group(1) if img else "",
            "tuesday": "화요음악회" in t,
        })

    # 라인업 글에만 있는 이후 화요음악회 일정
    if lineup_url:
        body = text(get(lineup_url))
        for m in re.finditer(r"(\d{1,2})월 (\d{1,2})일 \(화\) ([\d:]+ ?[AP]M)\s+(.+?)\s+\|\s+(.+?)\s{1,}[A-Z]", body):
            mo, d = int(m.group(1)), int(m.group(2))
            date = f"{guess_year(mo)}-{mo:02d}-{d:02d}"
            if any(e["tuesday"] and e["date"] == date for e in events):
                continue
            events.append({
                "date": date, "when": f"{mo}월 {d}일 (화) {m.group(3)}",
                "title": f"화요음악회 : {re.match(r'[^A-Za-z]*', m.group(4)).group(0).strip()} {m.group(5)}".strip(),
                "place": "서울대학교 음악대학 49동 콘서트홀",
                "url": lineup_url, "img": "", "tuesday": True,
            })

    today = NOW.strftime("%Y-%m-%d")
    events = [e for e in events if e["date"] >= today]
    events.sort(key=lambda e: e["date"])
    return events


# ---------- 포스트크로싱 ----------
def postcrossing():
    page = text(get("https://www.postcrossing.com/"))
    out = {}
    for key, pat in [("members", r"([\d,]+) members"),
                     ("traveling", r"([\d,]+) postcards traveling"),
                     ("received", r"([\d,]+) postcards received")]:
        m = re.search(pat, page)
        if m:
            out[key] = int(m.group(1).replace(",", ""))
    return out


# ---------- 서울중앙지법 ----------
COURT = "https://seoul.scourt.go.kr"


def post_cp949(url, data, opener):
    body = urllib.parse.urlencode(data, encoding="cp949").encode()
    req = urllib.request.Request(url, data=body, headers={**UA, "Referer": url,
                                 "Content-Type": "application/x-www-form-urlencoded"})
    with opener.open(req, timeout=30) as r:
        return r.read().decode("cp949", "replace")


def rows(page):
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        out.append(([text(td) for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)], tr))
    return out


def court():
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    url = COURT + "/jibubmgr/trial/new/TrialList.work"
    opener.open(urllib.request.Request(url, headers=UA), timeout=30).read()
    page = post_cp949(url, {"currentPage": "", "searchWord": "", "searchOption": "",
                            "bub_cd": "", "seqnum": "", "group_1": "형사"}, opener)
    benches = []
    for cells, _ in rows(page):
        if len(cells) >= 3 and "형사" in cells[0] and cells[1] not in ("", "-"):
            benches.append({"name": cells[0], "days": cells[1], "room": cells[2]})

    # 온라인 방청 신청 (방청권이 필요한 화제 재판)
    url = COURT + "/attend/AttendList.work"
    page = opener.open(urllib.request.Request(url, headers=UA), timeout=30).read().decode("cp949", "replace")
    hot, today = [], NOW.strftime("%Y.%m.%d")
    for cells, tr in rows(page):
        seq = re.search(r"goView\('(\d+)'\)", tr)
        if not seq or len(cells) < 5:
            continue
        m = re.match(r"(\d{4}\.\d\d\.\d\d)\s*(\d+)시\s*(\d+)분", cells[2])
        if not m or m.group(1) < today:
            continue
        detail = post_cp949(COURT + "/attend/AttendView.work",
                            {"currentPage": "1", "pageSize": "10", "bubCd": "000210",
                             "seqno": seq.group(1), "check": "m", "encode": "1"}, opener)
        info = {}
        for th, td in re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", detail, re.S):
            info[text(th)] = text(td)
        hot.append({
            "date": m.group(1).replace(".", "-"), "time": f"{int(m.group(2)):02d}:{m.group(3)}",
            "title": info.get("제목", ""), "bench": cells[1],
            "apply": cells[3], "status": cells[4], "seats": info.get("방청권수", ""),
        })
    hot.sort(key=lambda x: x["date"])
    return {"benches": benches, "hot": hot}


# ---------- 지오캐싱 (봉천역 근처) ----------
HOME = (37.4824, 126.9418)  # 봉천역
TYPES_KO = {
    "Traditional Cache": "일반", "Multi-cache": "여러 단계", "Mystery Cache": "퍼즐",
    "Unknown Cache": "퍼즐", "Letterbox Hybrid": "레터박스", "Wherigo Cache": "위리고(앱 게임)",
    "Earthcache": "지형 관찰", "Virtual Cache": "가상(통 없음)", "Event Cache": "모임",
    "Webcam Cache": "웹캠", "Mega-Event Cache": "큰 모임", "Cache In Trash Out Event": "청소 모임",
}


def geocaching():
    import math
    import time
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    z, n = 15, 2 ** 15
    lat0, lon0 = HOME
    tx = int((lon0 + 180) / 360 * n)
    ty = int((1 - math.asinh(math.tan(math.radians(lat0))) / math.pi) / 2 * n)

    cells = {}
    for x in range(tx - 3, tx + 4):
        for y in range(ty - 3, ty + 4):
            # 타일 그림을 먼저 받아야 그 타일의 캐시 정보가 나온다
            opener.open(urllib.request.Request(f"https://tiles01.geocaching.com/map.png?x={x}&y={y}&z={z}", headers=UA), timeout=30).read()
            req = urllib.request.Request(f"https://tiles01.geocaching.com/map.info?x={x}&y={y}&z={z}", headers=UA)
            with opener.open(req, timeout=30) as r:
                raw = r.read()
            if not raw:
                continue
            for key, items in json.loads(raw)["data"].items():
                cx, cy = map(int, key.strip("()").split(","))
                px, py = x * 256 + cx * 4 + 2, y * 256 + cy * 4 + 2
                for it in items:
                    cells.setdefault(it["i"], {"name": it["n"], "pts": []})["pts"].append((px, py))

    def to_latlon(px, py):
        lon = px / (256 * n) * 360 - 180
        lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * py / (256 * n)))))
        return lat, lon

    caches = []
    for code, c in cells.items():
        px = sum(p[0] for p in c["pts"]) / len(c["pts"])
        py = sum(p[1] for p in c["pts"]) / len(c["pts"])
        lat, lon = to_latlon(px, py)
        dist = math.hypot((lat - lat0) * 111.0, (lon - lon0) * 111.0 * math.cos(math.radians(lat0)))
        caches.append({"code": code, "name": c["name"], "lat": round(lat, 4), "lon": round(lon, 4),
                       "km": round(dist, 1), "url": f"https://www.geocaching.com/geocache/{code}"})
    caches = sorted((c for c in caches if c["km"] <= 3.5), key=lambda c: c["km"])

    logs = []
    for c in caches:
        time.sleep(0.3)
        page = opener.open(urllib.request.Request(c["url"], headers=UA), timeout=30).read().decode("utf-8", "replace")
        title = re.search(r"<title>\s*(.*?)\s*</title>", page, re.S)
        tm = re.search(r"\(([^()]*(?:Cache|cache|Earthcache|Hybrid))\)", title.group(1)) if title else None
        c["type"] = TYPES_KO.get(tm.group(1), tm.group(1)) if tm else ""
        c["premium"] = "Premium Member Only" in page or "premium-upsell" in page
        dt = re.findall(r'alt="([\d.]+) out of 5"', page)
        if len(dt) >= 2:
            c["difficulty"], c["terrain"] = dt[0], dt[1]
        size = re.search(r'title="Size: ([^"]+)"', page)
        c["size"] = size.group(1) if size else ""
        hid = re.search(r"(?:Hidden|Event Date)\s*:\s*(\d+)/(\d+)/(\d{4})", text(page))
        c["hidden"] = f"{hid.group(3)}-{int(hid.group(1)):02d}-{int(hid.group(2)):02d}" if hid else ""
        found = re.search(r'\\"logTypeID\\":2,\\"logTypeName\\":\\"Found it\\",\\"count\\":(\d+)', page)
        c["found"] = int(found.group(1)) if found else 0

        tok = re.search(r"userToken = '([^']+)'", page)
        c["last"] = None
        if tok:
            lb = opener.open(urllib.request.Request(
                f"https://www.geocaching.com/seek/geocache.logbook?tkn={tok.group(1)}&idx=1&num=5&sp=false&sf=false&decrypt=false",
                headers={**UA, "Referer": c["url"]}), timeout=30).read().decode("utf-8", "replace")
            for lg in json.loads(lb).get("data", []):
                if lg["LogType"] not in ("Found it", "Didn't find it", "Attended", "Webcam Photo Taken"):
                    continue
                mo, d, y = lg["Visited"].split("/")
                entry = {"date": f"{y}-{mo}-{d}", "type": lg["LogType"], "user": lg["UserName"],
                         "text": text(lg["LogText"])[:140]}
                logs.append({**entry, "code": c["code"], "name": c["name"], "url": c["url"]})
                if c["last"] is None:
                    c["last"] = {"date": entry["date"], "type": entry["type"]}
    logs.sort(key=lambda l: l["date"], reverse=True)
    return {"updated": NOW.strftime("%Y-%m-%d %H:%M"), "caches": caches, "logs": logs[:8]}


def main():
    data = {"updated": NOW.strftime("%Y-%m-%d %H:%M")}
    try:
        old = json.load(open("data.json", encoding="utf-8"))
    except Exception:
        old = {}
    # 지오캐싱은 요청이 많아서 12시간에 한 번만 새로 받는다
    geo_old = old.get("geo", {}).get("updated", "")
    geo_due = os.environ.get("FORCE_GEO") == "1" or not geo_old or NOW.replace(tzinfo=None) - datetime.strptime(geo_old, "%Y-%m-%d %H:%M") > timedelta(hours=12)
    for key, fn in [("kofa", kofa), ("snu", snu), ("postcrossing", postcrossing), ("court", court), ("geo", geocaching)]:
        if key == "geo" and not geo_due:
            data[key] = old["geo"]
            continue
        try:
            data[key] = fn()
            print(key, "ok", len(data[key]))
        except Exception as e:
            print(key, "실패:", e)
            data[key] = old.get(key, {} if key in ("postcrossing", "court", "geo") else [])
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
