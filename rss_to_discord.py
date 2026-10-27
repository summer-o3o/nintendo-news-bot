import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

# ── 출처 1: 닌텐도 코리아 공식 뉴스 (중복만 아니면 전부 전송) ──
OFFICIAL_URL = "https://www.nintendo.com/kr/news"
OFFICIAL_SOURCE = "닌텐도 코리아 공식"
OFFICIAL_MAX_AGE_DAYS = 2   # 오늘·어제 날짜 공지만 전송

# ── 출처 2: 한국닌텐도 공식 유튜브 (중요한 영상만 전송) ──
YOUTUBE_CHANNEL_ID = "UCRCK5FCJtomQT3b88jXI_DA"
YOUTUBE_FEED_URL = (
    f"https://www.youtube.com/feeds/videos.xml?channel_id={YOUTUBE_CHANNEL_ID}"
)
YOUTUBE_SOURCE = "한국닌텐도 유튜브"
YOUTUBE_MAX_AGE_HOURS = 24
# 제목에 이 단어가 있으면 무조건 제외 (광고 영상)
YOUTUBE_SKIP_WORDS = [r"\bCM\b", "TVCM", "광고"]
# 다이렉트 본편: "Nintendo Direct 2026.9.9", "젤다의 전설 40주년 Direct 2026.9.8"
DIRECT_PATTERN = re.compile(r"Direct\s*\d{4}\.\d{1,2}\.\d{1,2}", re.IGNORECASE)
# 다이렉트 개별 클립: "스타폭스 [Nintendo Direct 2026.9.9]", "... 【... Direct 2026.9.8】"
DIRECT_CLIP_PATTERN = re.compile(r"[\[【][^\]】]*Direct[^\]】]*[\]】]", re.IGNORECASE)

# ── 출처 3: 본체 시스템 업데이트 (새 버전이 나오면 바로 전송) ──
SYSTEM_UPDATE_PAGES = [
    ("Nintendo Switch 2", "https://support.nintendo.com/kr/switch2/update/index.html"),
    ("Nintendo Switch", "https://support.nintendo.com/kr/switch/system_update/index.html"),
]
SYSTEM_UPDATE_SOURCE = "닌텐도 서포트"
SYSTEM_UPDATE_MAX_AGE_DAYS = 7
# "23.0.0 (2026년 9월 10일 배포)" / "21.0.1（2025년 11월 25일 배포 시작）" / "22.0.0 (2026 3월 17일 배포)"
VERSION_PATTERN = re.compile(
    r"(\d{1,2}\.\d{1,2}\.\d{1,2})\s*[(（]\s*(\d{4})\s*년?\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*배포[^)）]*[)）]"
)

SEEN_FILE = "seen.json"
MAX_SEEN = 10000
SIMILARITY = 0.75
DEDUP_HOURS = 168
LOG_FILE = "log.md"
MAX_LOG_LINES = 2000
KST = timezone(timedelta(hours=9))

# 계정에서 사용 가능한 실제 Anthropic 모델 ID인지 확인하세요.
AI_MODEL = "claude-haiku-5-5"
AI_MAX_TITLES = 100

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
CHANNEL_IDS = [
    c.strip()
    for c in os.environ.get("DISCORD_CHANNEL_IDS", "").split(",")
    if c.strip()
]
BOT_UA = "DiscordBot (https://github.com/summer-o3o/nintendo-news-bot, 1.0)"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

# 닌텐도 서포트 페이지는 봇 표시가 있는 접속을 막아서(403) 일반 브라우저로 접속
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
MEDIA = "{http://search.yahoo.com/mrss/}"


def fetch(url):
    last_error = None

    for attempt in range(3):
        req = urllib.request.Request(
            url, headers={"User-Agent": BROWSER_UA}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.read()
        except Exception as exc:
            last_error = exc
            print(f"요청 실패 ({attempt + 1}/3) {url[:40]}: {exc}")
            time.sleep(5)

    raise last_error


def parse_official(html):
    """닌텐도 코리아 뉴스 목록 페이지에서 공지 목록을 뽑는다."""
    items = []
    blocks = re.findall(r'<a href="([^"]+)"[^>]*>(.*?)</a>', html, re.S)

    for href, block in blocks:
        # 공지 카드만 (메뉴 등 다른 링크는 제목 칸이 없음)
        if "ncmn-softUnit__name" not in block:
            continue

        title_m = re.search(r'ncmn-softUnit__name">(.*?)</div>', block, re.S)
        date_m = re.search(r'(\d{4})\.(\d{2})\.(\d{2})', block)
        img_m = re.search(r'background-image:url\(([^)]+)\)', block)

        if not title_m:
            continue

        title = re.sub(r"<[^>]+>", "", title_m.group(1))
        title = (title.replace("&amp;", "&").replace("&quot;", '"')
                 .replace("&#x27;", "'").replace("&lt;", "<").replace("&gt;", ">").strip())

        dt = None
        if date_m:
            y, mth, d = map(int, date_m.groups())
            dt = datetime(y, mth, d, tzinfo=KST)

        image = None
        if img_m:
            image = img_m.group(1).replace("&amp;", "&").strip("'\"")
            image = image.split("?")[0] + "?w=1200&fm=jpg"

        # 뉴스 기사 말고 게임 페이지·유튜브·포켓몬코리아로 바로 가는 공지도 있음
        href = href.replace("&amp;", "&").strip()
        link = "https://www.nintendo.com" + href if href.startswith("/") else href
        article = re.match(r"/kr/news/article/([A-Za-z0-9]+)", href)
        item_id = "NK:" + (article.group(1) if article else link)

        items.append({
            "id": item_id,
            "kind": "official",
            "title": title,
            "link": link,
            "image": image,
            "ts": None,
            "dt": dt,
            "source": OFFICIAL_SOURCE,
        })

    return items


def parse_youtube(xml_bytes):
    """유튜브 채널 RSS(Atom)에서 영상 목록을 뽑는다."""
    root = ET.fromstring(xml_bytes)
    items = []

    for entry in root.findall(f"{ATOM}entry"):
        video_id = (entry.findtext(f"{YT}videoId") or "").strip()
        title = (entry.findtext(f"{ATOM}title") or "").strip()
        published = entry.findtext(f"{ATOM}published")

        if not video_id or not title:
            continue

        link_el = entry.find(f"{ATOM}link")
        link = link_el.get("href") if link_el is not None else ""
        link = link or f"https://www.youtube.com/watch?v={video_id}"

        thumb = entry.find(f"{MEDIA}group/{MEDIA}thumbnail")
        image = thumb.get("url") if thumb is not None else None
        image = image or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

        dt = None
        ts = None
        if published:
            try:
                dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                ts = dt.isoformat()
            except ValueError:
                pass

        items.append({
            "id": "YT:" + video_id,
            "kind": "youtube",
            "title": title,
            "link": link,
            "image": image,
            "ts": ts,
            "dt": dt,
            "source": YOUTUBE_SOURCE,
        })

    return items


def html_to_text(html):
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<br\s*/?>|</(p|li|div|h\d)>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
            .replace("&#x27;", "'").replace("&lt;", "<").replace("&gt;", ">"))
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def parse_system_update(console, url, html):
    """서포트 페이지에서 가장 최신 시스템 버전 하나를 뽑는다."""
    text = html_to_text(html)
    matches = list(VERSION_PATTERN.finditer(text))

    if not matches:
        return []

    def version_key(m):
        return tuple(int(x) for x in m.group(1).split("."))

    top = max(version_key(m) for m in matches)

    def notes_of(m):
        # 다음 버전 제목이 나오기 전까지가 변경 내용
        following = [x.start() for x in matches if x.start() > m.end()]
        end = min(following) if following else m.end() + 800
        lines = []
        for line in text[m.end():end].splitlines():
            line = line.strip(" -・•")
            # 변경 내용이 끝나고 주의사항·안내가 시작되면 멈춤
            if "주의" in line or line.startswith(("본체 업데이트", "업데이트 내역")):
                break
            # 소제목이나 버전 번호만 있는 줄은 건너뜀
            if (len(line) <= 3 or re.fullmatch(r"\d+\.\d+\.\d+", line)
                    or line in ("업데이트 내용",) or line.startswith("이전 버전에서")):
                continue
            lines.append(f"• {line}")
        return "\n".join(lines)[:700]

    # 같은 버전이 "현재 버전" 안내와 본문 제목에 두 번 나올 수 있어 내용이 긴 쪽을 씀
    latest, notes = max(
        ((m, notes_of(m)) for m in matches if version_key(m) == top),
        key=lambda pair: len(pair[1]),
    )
    version = latest.group(1)
    y, mth, d = (int(latest.group(i)) for i in (2, 3, 4))

    return [{
        "id": f"SU:{console}:{version}",
        "kind": "system",
        "title": f"{console} 시스템 업데이트 {version}",
        "link": url,
        "image": None,
        "summary": notes,
        "ts": None,
        "dt": datetime(y, mth, d, tzinfo=KST),
        "source": SYSTEM_UPDATE_SOURCE,
    }]


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception as exc:
            print(f"seen.json을 읽지 못했습니다: {exc}")

    return None


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen[-MAX_SEEN:], f, ensure_ascii=False, indent=0)


def normalize(title):
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)|【[^】]*】", " ", title)
    text = re.sub(r"[^0-9a-zA-Z가-힣]", "", text).lower()

    for word in ("닌텐도", "nintendo", "스위치", "switch"):
        text = text.replace(word, "")

    return text


def bigrams(text):
    return {text[i:i + 2] for i in range(len(text) - 1)} or {text}


def similar(a, b):
    set_a, set_b = bigrams(a), bigrams(b)
    return len(set_a & set_b) / max(1, min(len(set_a), len(set_b)))


def recent_titles(seen, cutoff):
    result = []

    for entry in seen:
        if isinstance(entry, str) and entry.startswith("T:"):
            try:
                parts = entry[2:].split("|", 2)
                sent_at = datetime.fromisoformat(parts[0])

                if sent_at.tzinfo is None:
                    sent_at = sent_at.replace(tzinfo=timezone.utc)

                if sent_at >= cutoff:
                    normalized_title = parts[1]
                    original_title = (
                        parts[2] if len(parts) > 2 else parts[1]
                    )
                    result.append((normalized_title, original_title))

            except (ValueError, IndexError):
                continue

    return result


def write_log(date_str, block):
    """log.md 맨 위에 실행 기록을 추가한다. 같은 날짜면 그 날짜 제목 아래에 붙인다."""
    if not block:
        return

    old = []

    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, encoding="utf-8") as f:
            old = f.read().splitlines()

        if old and old[0].startswith("# "):
            old = old[2:]

    date_header = f"## 📅 {date_str}"
    entry = block + [""]

    if old and old[0] == date_header:
        body = old[:2] + entry + old[2:]
    elif old:
        # 날짜가 바뀌면 이전 날짜와 구분선으로 나눔
        body = [date_header, ""] + entry + ["---", ""] + old
    else:
        body = [date_header, ""] + entry

    header = ["# 닌텐도 뉴스 봇 실행 기록", ""]
    body = body[:MAX_LOG_LINES]

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(header + body).rstrip() + "\n")


DUPLICATE_RULES = (
    "[중복 판별]\n"
    "제목의 단어가 같은지만 비교하지 말고 같은 사건인지 판단한다.\n"
    "인물, 사건 종류, 핵심 사실, 발표 내용, 대상이 같으면 중복이다.\n"
    "같은 발표를 공지와 영상으로 각각 알리는 경우도 중복이다.\n"
    "다음은 별개의 소식이다.\n"
    "- 같은 게임이라도 사건 자체가 다른 소식 (출시일 확정과 출시 연기, 새 DLC 공개)\n"
    "- 행사·방송의 일정 예고와, 그 행사에서 실제로 발표된 내용\n"
    "- 기존 소식에 없던 중요한 신규 사실이 핵심인 후속 소식\n"
    "같은 사건인지 애매하면 가장 가까운 번호를 출력한다.\n\n"
)

YOUTUBE_RULES = (
    "[중요도 판별]\n"
    "이 영상은 한국닌텐도 공식 유튜브 채널에 새로 올라온 영상이다.\n"
    "닌텐도 유튜버들에게 바로 알릴 만큼 중요한 영상만 0을 출력한다. 그 밖에는 L.\n"
    "0 (중요):\n"
    "- 닌텐도 신작이나 대작의 첫 공개 영상, 발매일·발매 결정 발표 트레일러\n"
    "- 닌텐도 대형 게임의 유료 추가 콘텐츠(DLC) 첫 공개\n"
    "- 닌텐도(퍼스트파티) 게임이나 대형 게임의 소개 영상\n"
    "- 콜 오브 듀티, GTA, 엘든링처럼 게이머 대부분이 아는 대형 서드파티 게임의 "
    "스위치·스위치 2판 첫 공개·발매 발표 트레일러\n"
    "- 닌텐도 다이렉트 같은 발표 방송의 예고\n"
    "- 새 하드웨어나 한정판·특별 디자인 본체, 컨트롤러, 아미보 공개 "
    "(애니버서리 에디션, 스페셜 에디션, 세트 등)\n"
    "L (중요하지 않음):\n"
    "- 이미 공개된 게임의 플레이 영상, 론치 트레일러, 스토리·캠페인 트레일러\n"
    "- 중소 게임이나 인디 게임의 트레일러, 소개 영상\n"
    "- BGM, 음악, 메이킹, 인터뷰, 개발자 소개\n"
    "- 사용법, 다운로드 버전 안내 같은 가이드 영상\n"
    "- 콜라보, 이벤트, 굿즈, 체험판 홍보\n"
    "중요한지 애매하면 L을 출력한다.\n\n"
    "예시 (목록에 같은 사건이 없다고 가정):\n"
    "콜 오브 듀티: 모던 워페어 4 | Nintendo Switch 2 Trailer -> 0\n"
    "파이어 엠블렘 만자천홍 익스팬션 패스 첫 공개 영상 -> 0\n"
    "Nintendo Switch 2 마리오 카트 월드 스페셜 에디션 본체 공개 -> 0\n"
    "젤다의 전설 시간의 오카리나 소개 영상 -> 0\n"
    "[물거품의 유크로니아] 소개 영상 -> L\n"
    "Dragon's Dogma 2: Dark Arisen - Launch Trailer (한국어) -> L\n"
    "'ELDEN RING 빛바랜 자 에디션' 게임 플레이 트레일러 -> L\n"
    "마리오 카트 월드 BGM 레코딩 영상 「메인 테마」 -> L\n"
    "다운로드 버전 소프트웨어 안내: 본체 저장 메모리 빈 용량을 늘리는 방법 -> L\n\n"
)


def ai_check(title, recent, kind):
    """
    kind: "official"(중복만 판단) 또는 "youtube"(중복 + 중요도)
    반환값:
    ('dup', 기존 제목): 같은 사건
    ('low', None): 중요하지 않은 영상
    ('new', None): 보낼 소식
    ('error', None): AI 검사 실패
    """

    if not ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY가 없어 AI 검사를 할 수 없습니다.")
        return "error", None

    recent = recent[-AI_MAX_TITLES:]

    listing = "\n".join(
        f"{index + 1}. {original_title}"
        for index, (_, original_title) in enumerate(recent)
    ) or "(없음)"

    if kind == "official":
        task = (
            "너는 닌텐도 소식의 엄격한 중복 판별기다.\n"
            "새 소식은 닌텐도 코리아 홈페이지의 공식 공지다. "
            "기존 발송 목록에 같은 사건이 있는지만 판단한다.\n\n"
            + DUPLICATE_RULES
            + "반드시 아래 중 하나만 출력하라.\n"
            "숫자 = 기존 발송 목록에서 같은 사건인 소식의 번호\n"
            "0 = 새로운 소식\n"
        )
    else:
        task = (
            "너는 닌텐도 전문 유튜버들을 위한 뉴스 큐레이터이자 엄격한 중복 판별기다.\n"
            "먼저 중복인지 판단하고, 중복이 아니면 중요도를 판단한다.\n\n"
            + DUPLICATE_RULES
            + YOUTUBE_RULES
            + "반드시 아래 중 하나만 출력하라.\n"
            "숫자 = 기존 발송 목록에서 같은 사건인 소식의 번호\n"
            "L = 새롭지만 중요하지 않은 영상\n"
            "0 = 새롭고 중요한 영상\n"
        )

    prompt = (
        task
        + "설명, 마크다운, 따옴표 없이 판정값 하나만 출력하라.\n\n"
        f"기존 발송 목록:\n{listing}\n\n"
        f"새 소식 제목:\n{title}\n"
    )

    body = json.dumps({
        "model": AI_MODEL,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        method="POST",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            data = json.load(response)

        answer = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        ).strip().upper()

        answer = re.sub(r"^[`\s]+|[`\s]+$", "", answer)

        if answer.startswith("L"):
            # 공식 공지는 중요도와 관계없이 전송
            return ("new", None) if kind == "official" else ("low", None)

        match = re.match(r"\d+", answer)

        if match:
            number = int(match.group())

            if number == 0:
                return "new", None

            if 1 <= number <= len(recent):
                return "dup", recent[number - 1][1]

            # 목록에 없는 번호: 중복이라고 본 것이므로 보내지 않음
            return "dup", f"(AI가 없는 번호 {number}를 답함)"

        print(f"AI가 예상하지 못한 답변을 반환했습니다: {answer!r}")
        return "error", None

    except Exception as exc:
        print(f"AI 판단 실패, 발송을 보류합니다: {exc}")
        return "error", None


def make_embed(item):
    embed = {
        "title": item["title"][:256],
        "url": item["link"],
        "footer": {"text": item["source"]},
    }

    if item["kind"] == "official":
        embed["author"] = {"name": "📢 닌텐도 공식 공지"}
        embed["color"] = 0xFFC400
    elif item["kind"] == "system":
        embed["author"] = {"name": "🔧 본체 시스템 업데이트"}
        embed["color"] = 0x00C3E3
    else:
        embed["author"] = {"name": "▶️ 한국닌텐도 유튜브"}
        embed["color"] = 0xE60012

    if item.get("summary"):
        embed["description"] = item["summary"][:1000]

    if item["image"]:
        embed["image"] = {"url": item["image"]}

    if item["ts"]:
        embed["timestamp"] = item["ts"]

    return embed


def post(url, payload, headers):
    body = json.dumps(payload).encode("utf-8")

    for _ in range(3):
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", **headers},
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                response.read()
            return True

        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")

            if exc.code == 429:
                try:
                    wait = float(json.loads(detail).get("retry_after", 2))
                except Exception:
                    wait = 2

                time.sleep(wait + 0.5)
                continue

            print(f"전송 실패 {exc.code}: {detail[:300]}")
            return False

        except Exception as exc:
            print(f"전송 오류: {exc}")
            return False

    return False


def send(item):
    payload = {"embeds": [make_embed(item)]}
    destinations = 0
    success = True

    if WEBHOOK_URL:
        destinations += 1
        success = post(
            WEBHOOK_URL, payload, {"User-Agent": "news-bot"}
        ) and success

    for channel_id in CHANNEL_IDS:
        destinations += 1
        channel_success = post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            payload,
            {
                "Authorization": f"Bot {BOT_TOKEN}",
                "User-Agent": BOT_UA,
            },
        )
        success = channel_success and success

    return destinations > 0 and success


def youtube_skip_reason(item):
    """규칙으로 바로 거를 유튜브 영상이면 이유를, 아니면 None."""
    title = item["title"]

    if "/shorts/" in item["link"]:
        return "🎞️ 쇼츠"

    if any(re.search(word, title, re.IGNORECASE) for word in YOUTUBE_SKIP_WORDS):
        return "📺 CM·광고"

    if DIRECT_CLIP_PATTERN.search(title):
        return "🎬 다이렉트 개별 영상"

    return None


def main():
    if not WEBHOOK_URL and not (BOT_TOKEN and CHANNEL_IDS):
        sys.exit(
            "DISCORD_WEBHOOK_URL 또는 "
            "DISCORD_BOT_TOKEN + DISCORD_CHANNEL_IDS를 설정해 주세요."
        )

    if not ANTHROPIC_API_KEY:
        sys.exit(
            "중복 방지를 위해 ANTHROPIC_API_KEY가 필요합니다. "
            "환경변수를 설정해 주세요."
        )

    now = datetime.now(timezone.utc)
    local_now = now.astimezone(KST)
    log_date = local_now.strftime("%Y-%m-%d")
    log_time = local_now.strftime("%H:%M")

    # ── 두 출처 가져오기 (하나가 실패해도 다른 하나는 진행) ──
    official_items = []
    try:
        official_items = parse_official(fetch(OFFICIAL_URL).decode("utf-8", "replace"))
        print(f"닌텐도 공식 뉴스 {len(official_items)}개 확인")
    except Exception as exc:
        print(f"닌텐도 공식 뉴스를 가져오지 못했습니다: {exc}")

    youtube_items = []
    try:
        youtube_items = parse_youtube(fetch(YOUTUBE_FEED_URL))
        print(f"한국닌텐도 유튜브 영상 {len(youtube_items)}개 확인")
    except Exception as exc:
        print(f"한국닌텐도 유튜브를 가져오지 못했습니다: {exc}")

    system_items = []
    for console, url in SYSTEM_UPDATE_PAGES:
        try:
            found = parse_system_update(console, url, fetch(url).decode("utf-8", "replace"))
            print(f"{console} 시스템 버전 확인: {found[0]['title'] if found else '찾지 못함'}")
            system_items += found
        except Exception as exc:
            print(f"{console} 시스템 업데이트 페이지를 가져오지 못했습니다: {exc}")

    if not official_items and not youtube_items and not system_items:
        print("가져온 소식이 없어 이번 실행은 건너뜁니다.")
        return

    seen = load_seen()
    first_lines = []

    if seen is None:
        save_seen([item["id"] for item in official_items + youtube_items + system_items])
        print("첫 실행: 지금 있는 소식을 기록만 했습니다.")
        write_log(log_date, [
            f"**{log_time}** · 🆕 첫 실행: 소식 "
            f"{len(official_items) + len(youtube_items) + len(system_items)}개를 기록만 함",
        ])
        return

    def has_prefix(prefix):
        return any(isinstance(e, str) and e.startswith(prefix) for e in seen)

    # 출처를 처음 읽는 경우: 지금 있는 것은 기록만 (도배 방지)
    if official_items and not has_prefix("NK:"):
        seen.extend(item["id"] for item in official_items)
        first_lines.append(
            f"- 🆕 닌텐도 공식 뉴스 첫 확인: 공지 {len(official_items)}개를 기록만 함"
        )
        official_items = []

    if youtube_items and not has_prefix("YT:"):
        seen.extend(item["id"] for item in youtube_items)
        first_lines.append(
            f"- 🆕 한국닌텐도 유튜브 첫 확인: 영상 {len(youtube_items)}개를 기록만 함"
        )
        youtube_items = []

    if system_items and not has_prefix("SU:"):
        seen.extend(item["id"] for item in system_items)
        first_lines.append(
            "- 🆕 시스템 업데이트 첫 확인: "
            + ", ".join(item["title"] for item in system_items)
            + " 기록만 함"
        )
        system_items = []

    official_cutoff = (local_now - timedelta(days=OFFICIAL_MAX_AGE_DAYS - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    youtube_cutoff = now - timedelta(hours=YOUTUBE_MAX_AGE_HOURS)
    sent_titles = recent_titles(seen, now - timedelta(hours=DEDUP_HOURS))

    seen_set = set(seen)
    min_dt = datetime.min.replace(tzinfo=timezone.utc)
    new_official = [i for i in official_items if i["id"] not in seen_set]
    new_youtube = [i for i in youtube_items if i["id"] not in seen_set]
    new_official.sort(key=lambda item: item["dt"] or min_dt)
    new_youtube.sort(key=lambda item: item["dt"] or min_dt)
    # 공식 공지를 먼저 처리해서, 같은 소식의 영상은 중복으로 걸러지게 한다
    new_system = [i for i in system_items if i["id"] not in seen_set]
    system_cutoff = now - timedelta(days=SYSTEM_UPDATE_MAX_AGE_DAYS)
    new_items = new_system + new_official + new_youtube

    counts = {
        "old": 0, "rule": 0, "title_dup": 0, "ai_dup": 0, "low": 0, "held": 0,
    }
    sent_count = 0
    log = list(first_lines)

    for item in new_items:
        item_id = item["id"]
        kind = item["kind"]
        seen.append(item_id)
        label = f"{item['title']} · {item['source']}"

        if kind == "system":
            # 시스템 업데이트는 공식 정보라 판단 없이 바로 전송
            if item["dt"] < system_cutoff:
                counts["old"] += 1
                log.append(f"- ⏰ 지난 업데이트: {label}")
                continue

            if send(item):
                time.sleep(1.5)
                sent_count += 1
                log.append(f"- 🔧 업데이트 전송: {label}")
            else:
                seen.remove(item_id)
                counts["held"] += 1
                log.append(f"- ⚠️ 디스코드 전송 실패로 보류: {label}")
            continue

        if kind == "official":
            if item["dt"] is None or item["dt"] < official_cutoff:
                counts["old"] += 1
                log.append(f"- ⏰ 지난 공지: {label}")
                continue
        else:
            if item["dt"] is None or item["dt"] < youtube_cutoff:
                counts["old"] += 1
                log.append(f"- ⏰ {YOUTUBE_MAX_AGE_HOURS}시간 지난 영상: {label}")
                continue

            reason = youtube_skip_reason(item)
            if reason:
                counts["rule"] += 1
                log.append(f"- {reason}: {label}")
                continue

        normalized_title = normalize(item["title"])
        is_direct = kind == "youtube" and bool(DIRECT_PATTERN.search(item["title"]))

        match_score, match_title = max(
            (
                (similar(normalized_title, previous_normalized), original_title)
                for previous_normalized, original_title in sent_titles
            ),
            default=(0, ""),
        )

        if match_score >= SIMILARITY:
            counts["title_dup"] += 1
            log.append(
                f"- 🔁 제목 유사도 중복 {match_score:.0%}: {label} ← {match_title}"
            )
            continue

        # 다이렉트 본편은 중요도 판단 없이 전송
        if not is_direct:
            verdict, ai_match = ai_check(item["title"], sent_titles, kind)

            if verdict == "error":
                if item_id in seen:
                    seen.remove(item_id)
                counts["held"] += 1
                log.append(f"- ⚠️ AI 검사 실패로 발송 보류: {label}")
                continue

            if verdict == "dup":
                counts["ai_dup"] += 1
                log.append(f"- 🤖 AI 중복: {label} ← {ai_match}")
                continue

            if verdict == "low":
                counts["low"] += 1
                log.append(f"- 💤 중요도 낮음: {label}")
                continue

        if send(item):
            time.sleep(1.5)
            sent_count += 1
            sent_titles.append((normalized_title, item["title"]))
            seen.append(f"T:{now.isoformat()}|{normalized_title}|{item['title']}")
            mark = "📢 공식 전송" if kind == "official" else "▶️ 유튜브 전송"
            log.append(f"- {mark}: {label}")
        else:
            if item_id in seen:
                seen.remove(item_id)
            counts["held"] += 1
            log.append(f"- ⚠️ 디스코드 전송 실패로 보류: {label}")

    save_seen(seen)

    names = [
        ("🔁 제목 유사 중복", "title_dup"),
        ("🤖 AI 중복", "ai_dup"),
        ("💤 중요도 낮음", "low"),
        ("📺 규칙 제외", "rule"),
        ("⏰ 시간 초과", "old"),
        ("⚠️ 보류", "held"),
    ]
    detail = " · ".join(f"{name} {counts[key]}" for name, key in names if counts[key])
    print(f"새 소식 {len(new_items)}개 중 {sent_count}개 전송 ({detail or '제외 없음'})")

    if log:
        headline = f"**{log_time}** · 새 소식 {len(new_items)}개 중 **{sent_count}개 전송**"
        if detail:
            headline += f" · {detail}"
        write_log(log_date, [headline, ""] + log)


if __name__ == "__main__":
    main()
