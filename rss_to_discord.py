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

# Google News RSS
FEED_URL = (
    "https://news.google.com/rss/search?q=%EB%8B%8C%ED%85%90%EB%8F%84+when:1d"
    "&hl=ko&gl=KR&ceid=KR:ko"
)

MAX_AGE_HOURS = 3
KEYWORDS = r""

EXCLUDE_SOURCES = [
    "부산일보", "국제신문", "경남신문", "경남도민일보", "경상일보", "울산매일",
    "매일신문", "영남일보", "대구일보", "경북일보", "경북매일", "경북도민일보",
    "경인일보", "인천일보", "기호일보", "중부일보", "경기일보", "경기신문",
    "강원일보", "강원도민일보",
    "대전일보", "중도일보", "충청투데이", "충북일보", "충청일보", "동양일보",
    "충남일보", "광주일보", "무등일보", "남도일보", "전남일보", "전북일보",
    "전라일보", "새전북신문", "전북도민일보", "제주일보", "한라일보", "제민일보",
]

# 단어만으로 기사를 무조건 제외하지 않고 AI가 관련성을 판단
EXCLUDE_TITLE_WORDS = []

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


def fetch(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (news-bot)"}
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


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
            match = re.search(r'<img[^>]+src="([^"]+)"', desc)
            if match:
                image = match.group(1)

        # Google News RSS description은 제목 반복인 경우가 많음
        summary = ""
        ts = None
        dt = None

        if pub:
            try:
                dt = parsedate_to_datetime(pub)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ts = dt.isoformat()
            except Exception:
                pass

        items.append({
            "id": guid,
            "title": title,
            "link": link,
            "image": image,
            "summary": summary,
            "ts": ts,
            "dt": dt,
            "source": source or "구글 뉴스",
        })

    return items


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
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", title)
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


def ai_check(title, recent):
    """
    반환값:
    ('off', None): 관련 없는 기사
    ('dup', 기존 제목): 같은 사건
    ('new', None): 새로운 소식
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

    prompt = (
        "너는 닌텐도 전문 뉴스 편집자이자 엄격한 중복 뉴스 판별기다.\n"
        "제공된 제목을 분석해 닌텐도·닌텐도 스위치가 핵심인 새로운 소식만 선별한다.\n\n"

        "[1단계: 관련성 판별]\n"
        "닌텐도 또는 닌텐도 스위치가 단순히 언급되는 것이 아니라 핵심 주제여야 한다.\n"
        "다음 소식은 포함한다.\n"
        "- 닌텐도의 신제품, 게임, 공식 발표, 출시, 업데이트, 실적, 개발진 및 경영 관련 소식\n"
        "- 닌텐도 스위치·스위치 2로 출시되거나 출시 예정인 다른 회사 게임 소식\n"
        "  (출시일, 한국어판, 체험판, 업데이트, 판매 성과 등. 여러 기종 동시 출시도 포함)\n\n"

        "다음은 제외한다.\n"
        "- 은행, 카드사, 쇼핑몰, 통신사 등 다른 기업의 판촉 행사\n"
        "- 닌텐도 스위치가 경품, 사은품, 증정품으로만 등장하는 기사\n"
        "- 다른 기업의 광고나 구매 혜택이 핵심인 기사\n"
        "- 닌텐도와 직접 관련 없는 기사\n\n"

        "예시:\n"
        "하나은행, 닌텐도 스위치 증정 이벤트 -> X\n"
        "카드사, 닌텐도 스위치 경품 행사 -> X\n"
        "닌텐도, 스위치 2 신제품 발표 -> 0 (목록에 같은 사건이 없을 때)\n"
        "아크시스템웍스, 'OO' 닌텐도 스위치 한국어판 출시 -> 0 (목록에 같은 사건이 없을 때)\n"
        "소니, PS5 판매량 증가 -> X\n\n"

        "[2단계: 사건 단위 중복 판별]\n"
        "제목의 단어가 같은지만 비교하지 말고 같은 사건인지 판단한다.\n"
        "인물, 사건 종류, 핵심 사실, 발표 내용, 대상이 같으면 중복이다.\n"
        "다른 언론사의 재보도, 제목 변경, 단순 요약 및 재작성도 중복이다.\n"
        "같은 사건에 대한 단순 반응, 소감, 해설도 중복으로 본다.\n\n"

        "중복 예시:\n"
        "기존: 미야모토 시게루 문화훈장 수훈\n"
        "신규: 슈퍼 마리오 만든 게임 전설 문화훈장 받는다\n"
        "판정: 동일 인물의 동일한 문화훈장 수훈 소식이므로 중복\n\n"

        "별개일 수 있는 소식:\n"
        "- 같은 게임의 서로 다른 업데이트\n"
        "- 서로 다른 공식 발표\n"
        "- 실제로 다른 판매 실적이나 지역별 통계\n"
        "- 기존 기사에 없던 중요한 신규 사실이 핵심인 후속 보도\n\n"

        "제목이 다르다는 이유만으로 신규라고 판단하지 마라.\n"
        "공통 단어만 있다는 이유로 중복이라고 판단하지도 마라.\n\n"

        "[애매할 때]\n"
        "확실하게 새로운 소식일 때만 0을 출력한다. 애매하면 보내지 않는다.\n"
        "- 닌텐도·닌텐도 스위치가 핵심인지 애매하면 X를 출력한다.\n"
        "- 기존 기사와 같은 사건인지 애매하면 가장 가까운 기사의 번호를 출력한다.\n\n"

        "반드시 아래 중 하나만 출력하라.\n"
        "X = 관련 없는 기사\n"
        "숫자 = 기존 발송 기사 목록에서 같은 사건인 기사의 번호\n"
        "0 = 관련 있는 새로운 소식\n"
        "설명, 마크다운, 따옴표 없이 판정값 하나만 출력하라.\n\n"

        f"기존 발송 기사 목록:\n{listing}\n\n"
        f"새 기사 제목:\n{title}\n"
    )

    body = json.dumps({
        "model": AI_MODEL,
        "max_tokens": 20,
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

        # "X", "0", "2", "2번", "2." 같은 답을 모두 처리
        if answer.startswith("X"):
            return "off", None

        match = re.match(r"\d+", answer)

        if match:
            number = int(match.group())

            if number == 0:
                return "new", None

            if 1 <= number <= len(recent):
                return "dup", recent[number - 1][1]

            # 목록에 없는 번호: 중복이라고 본 것이므로 애매하면 보내지 않는 원칙대로 건너뜀
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
        "color": 0xE60012,
        "footer": {"text": item["source"]},
    }

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

    items = parse_items(fetch(FEED_URL))
    pattern = re.compile(KEYWORDS, re.IGNORECASE) if KEYWORDS else None
    seen = load_seen()

    now = datetime.now(timezone.utc)
    stamp = now.astimezone(KST).strftime("%Y-%m-%d %H:%M")

    if seen is None:
        save_seen([item["id"] for item in items])
        print(f"첫 실행: 기사 {len(items)}개를 기록만 했습니다.")
        write_log([
            f"## {stamp}",
            f"- 🆕 첫 실행: 기사 {len(items)}개를 기록만 함",
        ])
        return

    cutoff = now - timedelta(hours=MAX_AGE_HOURS)
    sent_titles = recent_titles(
        seen, now - timedelta(hours=DEDUP_HOURS)
    )

    seen_set = set(seen)
    new_items = [
        item for item in items if item["id"] not in seen_set
    ]

    min_dt = datetime.min.replace(tzinfo=timezone.utc)
    new_items.sort(key=lambda item: item["dt"] or min_dt)

    sent_count = 0
    duplicate_count = 0
    ai_duplicate_count = 0
    skipped = 0
    off = 0
    old_count = 0
    held = 0
    log = []

    for item in new_items:
        item_id = item["id"]
        seen.append(item_id)
        label = f"{item['title']} · {item['source']}"

        if item["dt"] is None or item["dt"] < cutoff:
            old_count += 1
            log.append(f"- ⏰ {MAX_AGE_HOURS}시간 지남: {label}")
            continue

        if KEYWORDS and not pattern.search(item["title"]):
            log.append(f"- 🔤 키워드 없음: {label}")
            continue

        if any(name in item["source"] for name in EXCLUDE_SOURCES):
            skipped += 1
            log.append(f"- 🚫 제외 언론사: {label}")
            continue

        excluded_word = next(
            (word for word in EXCLUDE_TITLE_WORDS if word in item["title"]),
            None,
        )

        if excluded_word:
            off += 1
            log.append(f"- 🙅 제외 단어 '{excluded_word}': {label}")
            continue

        normalized_title = normalize(item["title"])

        match_score, match_title = max(
            (
                (similar(normalized_title, previous_normalized), original_title)
                for previous_normalized, original_title in sent_titles
            ),
            default=(0, ""),
        )

        if match_score >= SIMILARITY:
            duplicate_count += 1
            print(f"비슷한 기사라 건너뜁니다: {item['title']}")
            log.append(
                f"- 🔁 제목 유사도 중복 {match_score:.0%}: "
                f"{label} ← {match_title}"
            )
            continue

        verdict, ai_match = ai_check(item["title"], sent_titles)

        if verdict == "error":
            if item_id in seen:
                seen.remove(item_id)

            held += 1
            log.append(f"- ⚠️ AI 검사 실패로 발송 보류: {label}")
            continue

        if verdict == "off":
            off += 1
            print(f"AI가 관련 없는 기사라 판단해 건너뜁니다: {item['title']}")
            log.append(f"- 🙅 AI 관련 없음: {label}")
            continue

        if verdict == "dup":
            ai_duplicate_count += 1
            print(f"AI가 같은 소식이라 판단해 건너뜁니다: {item['title']}")
            log.append(f"- 🤖 AI 중복: {label} ← {ai_match}")
            continue

        if send(item):
            time.sleep(1.5)
            sent_count += 1
            sent_titles.append((normalized_title, item["title"]))
            seen.append(
                f"T:{now.isoformat()}|{normalized_title}|{item['title']}"
            )
            log.append(f"- ✅ 전송: {label}")

        else:
            if item_id in seen:
                seen.remove(item_id)

            held += 1
            log.append(f"- ⚠️ 디스코드 전송 실패로 보류: {label}")

    save_seen(seen)

    summary = (
        f"새 기사 {len(new_items)}개 중 {sent_count}개 전송 "
        f"(제목 유사 중복 {duplicate_count}개, "
        f"AI 중복 {ai_duplicate_count}개, 관련 없음 {off}개, "
        f"제외 언론사 {skipped}개, "
        f"{MAX_AGE_HOURS}시간 초과 {old_count}개, "
        f"발송 보류 {held}개)"
    )

    print(summary)

    if log:
        write_log([f"## {stamp} — {summary}"] + log)


if __name__ == "__main__":
    main()