"""새로움 공급원 데이터 수집 → data.json"""
import html
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


def main():
    data = {"updated": NOW.strftime("%Y-%m-%d %H:%M")}
    try:
        old = json.load(open("data.json", encoding="utf-8"))
    except Exception:
        old = {}
    for key, fn in [("kofa", kofa), ("snu", snu), ("postcrossing", postcrossing), ("court", court)]:
        try:
            data[key] = fn()
            print(key, "ok", len(data[key]))
        except Exception as e:
            print(key, "실패:", e)
            data[key] = old.get(key, {} if key in ("postcrossing", "court") else [])
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
