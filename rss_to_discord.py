"""구글 뉴스 '닌텐도' 검색 RSS의 새 기사를 디스코드로 보내는 스크립트.

봇(DISCORD_BOT_TOKEN + DISCORD_CHANNEL_IDS)과 웹후크(DISCORD_WEBHOOK_URL)
둘 다 지원해요. 설정된 곳 모두에 보내요.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# when:1d = 최근 하루 기사만 검색
FEED_URL = "https://news.google.com/rss/search?q=%EB%8B%8C%ED%85%90%EB%8F%84+when:1d&hl=ko&gl=KR&ceid=KR:ko"
# 이 시간보다 오래된 기사는 보내지 않음
MAX_AGE_HOURS = 3
# 비워두면 전부 보냄. 거르고 싶으면 예: r"스위치|마리오"
KEYWORDS = r""
# 받지 않을 언론사 (이름에 이 글자가 들어가면 제외). 거슬리는 곳이 보이면 추가하세요
EXCLUDE_SOURCES = [
    # 부산·울산·경남
    "부산일보", "국제신문", "경남신문", "경남도민일보", "경상일보", "울산매일",
    # 대구·경북
    "매일신문", "영남일보", "대구일보", "경북일보", "경북매일", "경북도민일보",
    # 경기·인천
    "경인일보", "인천일보", "기호일보", "중부일보", "경기일보", "경기신문",
    # 강원
    "강원일보", "강원도민일보",
    # 대전·충청
    "대전일보", "중도일보", "충청투데이", "충북일보", "충청일보", "동양일보", "충남일보",
    # 광주·전라
    "광주일보", "무등일보", "남도일보", "전남일보", "전북일보", "전라일보", "새전북신문", "전북도민일보",
    # 제주
    "제주일보", "한라일보", "제민일보",
]
SEEN_FILE = "seen.json"
MAX_SEEN = 3000
# 제목이 이 정도 이상 비슷하면 같은 소식으로 보고 안 보냄 (0~1, 높을수록 엄격)
SIMILARITY = 0.6
# 이 시간 안에 보낸 기사들과 비교
DEDUP_HOURS = 24
# 실행 기록 파일 (data 브랜치에 저장, 최신이 위). 이 줄 수까지만 보관
LOG_FILE = "log.md"
MAX_LOG_LINES = 2000
KST = timezone(timedelta(hours=9))

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
CHANNEL_IDS = [c.strip() for c in os.environ.get("DISCORD_CHANNEL_IDS", "").split(",") if c.strip()]
BOT_UA = "DiscordBot (https://github.com/summer-o3o/nintendo-news-bot, 1.0)"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (news-bot)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def parse_items(xml_bytes):
    root = ET.fromstring(xml_bytes)
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        source = (it.findtext("source") or "").strip()
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)]
        link = (it.findtext("link") or "").strip()
        guid = (it.findtext("guid") or link).strip()
        desc = it.findtext("description") or ""
        pub = it.findtext("pubDate")

        image = None
        enc = it.find("enclosure")
        if enc is not None and enc.get("url"):
            image = enc.get("url")
        if not image:
            m = re.search(r'<img[^>]+src="([^"]+)"', desc)
            if m:
                image = m.group(1)

        # 구글 뉴스 description은 제목 반복이라 요약으로 쓰지 않음
        summary = ""

        ts = None
        dt = None
        if pub:
            try:
                dt = parsedate_to_datetime(pub)
                ts = dt.isoformat()
            except Exception:
                pass

        items.append({"id": guid, "title": title, "link": link,
                      "image": image, "summary": summary, "ts": ts, "dt": dt,
                      "source": source or "구글 뉴스"})
    return items


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception as e:
            print(f"seen.json을 읽지 못해 새로 시작해요: {e}")
    return None


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen[-MAX_SEEN:], f, ensure_ascii=False, indent=0)


def normalize(title):
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", title)   # [단독], (종합) 등 제거
    t = re.sub(r"[^0-9a-zA-Z가-힣]", "", t).lower()
    # 거의 모든 기사에 들어가는 단어는 비교에서 빼기
    for w in ("닌텐도", "nintendo", "스위치", "switch"):
        t = t.replace(w, "")
    return t


def bigrams(t):
    return {t[i:i + 2] for i in range(len(t) - 1)} or {t}


def similar(a, b):
    A, B = bigrams(a), bigrams(b)
    return len(A & B) / max(1, min(len(A), len(B)))


def recent_titles(seen, cutoff):
    # seen.json 안에 "T:<시각>|<정리된 제목>|<원래 제목>" 형태로 보낸 기사 제목도 함께 저장
    out = []
    for s in seen:
        if isinstance(s, str) and s.startswith("T:"):
            try:
                parts = s[2:].split("|", 2)
                if datetime.fromisoformat(parts[0]) >= cutoff:
                    out.append((parts[1], parts[2] if len(parts) > 2 else parts[1]))
            except Exception:
                pass
    return out


def write_log(lines):
    if not lines:
        return
    old = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, encoding="utf-8") as f:
            old = f.read().splitlines()
        if old and old[0].startswith("# "):
            old = old[2:]
    header = ["# 닌텐도 뉴스 봇 실행 기록", ""]
    body = (lines + [""] + old)[:MAX_LOG_LINES]
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(header + body).rstrip() + "\n")


def make_embed(item):
    embed = {"title": item["title"][:256], "url": item["link"],
             "color": 0xE60012, "footer": {"text": item["source"]}}
    if item["summary"]:
        embed["description"] = item["summary"]
    if item["image"]:
        embed["image"] = {"url": item["image"]}
    if item["ts"]:
        embed["timestamp"] = item["ts"]
    return embed


def post(url, payload, headers):
    body = json.dumps(payload).encode("utf-8")
    for _ in range(3):
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", **headers})
        try:
            urllib.request.urlopen(req, timeout=30).read()
            return True
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            if e.code == 429:  # 너무 빨리 보냄 → 잠깐 기다렸다 다시
                try:
                    wait = float(json.loads(detail).get("retry_after", 2))
                except Exception:
                    wait = 2
                time.sleep(wait + 0.5)
                continue
            print(f"전송 실패 {e.code}: {detail[:300]}")
            return False
    return False


def send(item):
    payload = {"embeds": [make_embed(item)]}
    ok = True
    if WEBHOOK_URL:
        ok &= post(WEBHOOK_URL, payload, {"User-Agent": "news-bot"})
    for cid in CHANNEL_IDS:
        ok &= post(f"https://discord.com/api/v10/channels/{cid}/messages", payload,
                   {"Authorization": f"Bot {BOT_TOKEN}", "User-Agent": BOT_UA})
    return ok


def main():
    if not WEBHOOK_URL and not (BOT_TOKEN and CHANNEL_IDS):
        sys.exit("DISCORD_WEBHOOK_URL 또는 DISCORD_BOT_TOKEN + DISCORD_CHANNEL_IDS를 설정해 주세요.")
    items = parse_items(fetch(FEED_URL))
    pattern = re.compile(KEYWORDS, re.IGNORECASE) if KEYWORDS else None
    seen = load_seen()

    now = datetime.now(timezone.utc)
    stamp = now.astimezone(KST).strftime("%Y-%m-%d %H:%M")

    if seen is None:
        # 첫 실행: 기존 기사로 도배되지 않게 기록만 하고 끝냄
        save_seen([i["id"] for i in items])
        print(f"첫 실행: 기사 {len(items)}개를 기록만 했어요.")
        write_log([f"## {stamp}", f"- 🆕 첫 실행: 기사 {len(items)}개를 기록만 함"])
        return

    cutoff = now - timedelta(hours=MAX_AGE_HOURS)
    sent_titles = recent_titles(seen, now - timedelta(hours=DEDUP_HOURS))
    seen_set = set(seen)
    new = [i for i in items if i["id"] not in seen_set]
    # 오래된 기사부터 순서대로 올리기
    min_dt = datetime.min.replace(tzinfo=timezone.utc)
    new.sort(key=lambda i: i["dt"] or min_dt)
    sent = dup = skipped = old_cnt = 0
    log = []
    for item in new:
        seen.append(item["id"])
        label = f"{item['title']} · {item['source']}"
        too_old = item["dt"] is None or item["dt"] < cutoff
        if too_old:
            old_cnt += 1
            log.append(f"- ⏰ {MAX_AGE_HOURS}시간 지남: {label}")
            continue
        if KEYWORDS and not pattern.search(item["title"]):
            log.append(f"- 🔤 키워드 없음: {label}")
            continue
        if any(name in item["source"] for name in EXCLUDE_SOURCES):
            skipped += 1
            log.append(f"- 🚫 제외 언론사: {label}")
            continue
        norm = normalize(item["title"])
        match = max(((similar(norm, n), orig) for n, orig in sent_titles), default=(0, ""))
        if match[0] >= SIMILARITY:
            dup += 1
            print(f"비슷한 기사라 건너뜀: {item['title']}")
            log.append(f"- 🔁 중복 {match[0]:.0%}: {label}  ← {match[1]}")
            continue
        send(item)
        time.sleep(1.5)  # 디스코드 전송 제한 방지
        sent += 1
        sent_titles.append((norm, item["title"]))
        seen.append(f"T:{now.isoformat()}|{norm}|{item['title']}")
        log.append(f"- ✅ 전송: {label}")
    save_seen(seen)
    summary = (f"새 기사 {len(new)}개 중 {sent}개 전송 "
               f"(비슷한 기사 {dup}개, 제외 언론사 {skipped}개, {MAX_AGE_HOURS}시간 지남 {old_cnt}개 건너뜀)")
    print(summary)
    if log:
        write_log([f"## {stamp} — {summary}"] + log)


if __name__ == "__main__":
    main()