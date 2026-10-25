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
    last_error = None

    for attempt in range(3):
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (news-bot)"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.read()
        except Exception as exc:
            last_error = exc
            print(f"구글 뉴스 요청 실패 ({attempt + 1}/3): {exc}")
            time.sleep(5)

    raise last_error


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
    ('low', None): 관련 있지만 화제성이 낮은 기사
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
        "너는 닌텐도 전문 유튜버들을 위한 뉴스 큐레이터이자 엄격한 중복 뉴스 판별기다.\n"
        "닌텐도 유튜버가 보고 '이건 영상으로 다룰 만하다'고 느낄 새로운 소식만 골라낸다.\n"
        "아래 1~3단계를 순서대로 판단한다.\n\n"

        "[1단계: 관련성 판별]\n"
        "닌텐도 또는 닌텐도 스위치가 단순히 언급되는 것이 아니라 핵심 주제여야 한다.\n"
        "다음 소식은 관련 있는 것으로 본다.\n"
        "- 닌텐도의 신제품, 게임, 공식 발표, 출시, 업데이트, 실적, 개발진 및 경영 관련 소식\n"
        "- 닌텐도 대표 게임 시리즈 소식. 제목에 '닌텐도'가 없어도 닌텐도 소식으로 본다.\n"
        "  (마리오, 젤다, 포켓몬, 커비, 동물의 숲, 스플래툰, 메트로이드, 피크민, 동키콩,\n"
        "   파이어 엠블렘, 제노블레이드, 스매시브라더스, 별의 커비, 요시, 와리오, 마리오카트 등)\n"
        "  이 목록은 일부 예시이며, 닌텐도가 개발하거나 유통하는 다른 게임도 모두 포함한다.\n"
        "- 닌텐도 스위치·스위치 2로 출시되거나 출시 예정인 다른 회사 게임 소식\n"
        "  (이식 가능성, 이식 루머, 스위치판 성능·용량 이야기도 포함)\n"
        "- 닌텐도 기기나 게임의 판매·할인·가격 소식, 판매 순위와 경쟁 기종 비교 소식\n"
        "- 닌텐도 IP의 영화, 테마파크, e스포츠, 대형 콜라보 소식\n"
        "- 닌텐도 IP를 쓴 모바일 게임·카드 게임 소식 (포켓몬 고, 포켓몬 카드 게임 등)\n\n"

        "다음은 관련 없는 것으로 보고 X를 출력한다.\n"
        "- 닌텐도 스위치가 경품, 사은품, 추첨 상품으로만 등장하는 다른 기업의 이벤트\n"
        "  (은행 적금·카드·통신사 가입, 앱 설치, 설문 참여 등을 하면 스위치를 주는 행사)\n"
        "- 다른 기업의 상품이나 서비스 홍보가 핵심이고 닌텐도는 미끼에 불과한 기사\n"
        "- 닌텐도와 직접 관련 없는 기사 (닌텐도 IP도 스위치도 아닌 PS·Xbox·PC·모바일 게임 소식 등)\n\n"

        "[2단계: 사건 단위 중복 판별]\n"
        "제목의 단어가 같은지만 비교하지 말고 같은 사건인지 판단한다.\n"
        "인물, 사건 종류, 핵심 사실, 발표 내용, 대상이 같으면 중복이다.\n"
        "다른 언론사의 재보도, 제목 변경, 단순 요약 및 재작성도 중복이다.\n"
        "같은 사건에 대한 단순 반응, 소감, 해설도 중복으로 본다.\n"
        "정리 기사는 이미 보낸 기사와 같은 내용을 다시 정리한 경우에만 중복이다.\n"
        "예: '미야모토 시게루 문화훈장 수훈'과 '슈퍼 마리오 만든 게임 전설 문화훈장 받는다'는 중복.\n"
        "이 원리는 출시, 발표, 평가, 판매, 할인, 인사, 행사 등 모든 사건에 똑같이 적용한다.\n"
        "다음은 별개의 소식이다.\n"
        "- 같은 게임이라도 사건 자체가 다른 소식 (출시일 확정과 출시 연기, 평론가 점수와 유저 반발)\n"
        "- 행사·방송의 일정 예고와, 그 행사에서 실제로 발표된 내용\n"
        "  (예: '다이렉트 12일 방송 확정'과 '다이렉트 총정리…신작 12종 공개'는 별개)\n"
        "- 지역, 숫자, 차수, 판매처가 다른 소식 (일본 판매량과 전 세계 판매량, 다른 쇼핑몰의 할인)\n"
        "- 기존 기사에 없던 중요한 신규 사실이 핵심인 후속 보도\n"
        "제목이 다르다는 이유만으로 신규라고 판단하지 말고, 공통 단어만으로 중복이라고 판단하지도 마라.\n\n"

        "[3단계: 화제성 판별]\n"
        "판단 기준은 하나다. 닌텐도 유튜버가 이 제목을 보고 "
        "'이건 영상으로 다루거나 팬들에게 공유할 만하다'고 느끼겠는가.\n"
        "다음 질문 중 하나라도 '그렇다'면 화제성이 높다 (0 출력).\n"
        "- 팬들이 처음 듣는 새로운 사실이거나 예상 밖의 소식인가?\n"
        "- 팬들의 계획이나 지갑, 기기 사용에 영향을 주는가? (출시일, 가격, 구매 정보, 저장 공간, 성능 등)\n"
        "- 팬들이 이야기하고 반응할 거리가 있는가? (기록, 논란, 평가, 루머, 주요 인물의 거취 등)\n"
        "콜 오브 듀티, GTA처럼 다른 기종에서 유명한 대형 게임의 스위치판 소식은 "
        "출시, 이식 가능성, 용량, 성능 등 무엇이든 그 자체로 팬들 관심이 크다.\n"
        "반대로 다음에 가까우면 화제성이 낮다 (L 출력).\n"
        "- 팬 입장에서 새로울 게 없는 일상적·형식적인 소식\n"
        "- 기업 홍보나 보도자료를 그대로 옮긴 기사\n"
        "- 극소수만 관심 가질 아주 작은 변화\n"
        "아래는 이해를 돕기 위한 예시일 뿐이며, 여기에 없는 소식도 위 기준으로 판단한다.\n"
        "- 높음 예: 신작 공개, 출시 연기, 가격 인상, 판매 기록, 평가 논란, 유출, 소송, 영화\n"
        "- 낮음 예: 단순 버그 수정 패치, 굿즈·콜라보 메뉴, 소폭 주가 변동, 보도자료성 홍보\n"
        "할인·특가·세일·쿠폰·적립·예약 판매·재입고 등 닌텐도 제품을 사는 데 도움이 되는 "
        "소식은 화제성과 관계없이 0으로 본다. (관련성과 중복 판단은 그대로 적용)\n\n"

        "[애매할 때]\n"
        "관련성과 중복은 애매하면 보내지 않는다. 화제성은 애매하면 보낸다.\n"
        "- 닌텐도·닌텐도 스위치가 핵심인지 애매하면 X를 출력한다.\n"
        "- 기존 기사와 같은 사건인지 애매하면 가장 가까운 기사의 번호를 출력한다.\n"
        "- 화제성이 애매하면 0을 출력한다. 누가 봐도 시시한 소식만 L로 거른다.\n\n"

        "아래 예시는 판단 원리를 보여주기 위한 것이며, 예시와 다른 경우도 같은 원리로 판단한다.\n"
        "예시 (목록에 같은 사건이 없다고 가정):\n"
        "하나은행, 적금 가입하면 닌텐도 스위치 증정 이벤트 -> X\n"
        "소니, PS5 판매량 증가 -> X\n"
        "닌텐도 다이렉트서 메트로이드 신작 깜짝 공개 -> 0\n"
        "젤다 신작 출시 연기…내년으로 -> 0\n"
        "쿠팡, 스위치 2 출시 후 첫 할인…역대 최저가 -> 0\n"
        "PS5, 스위치 2 제치고 판매량 1위 -> 0\n"
        "G마켓, 스위치 게임 5% 쿠폰 지급 -> 0\n"
        "닌텐도, 스플래툰 3 버그 수정 패치 배포 -> L\n"
        "편의점, 포켓몬 콜라보 도시락 출시 -> L\n\n"

        "반드시 아래 중 하나만 출력하라.\n"
        "X = 관련 없는 기사\n"
        "숫자 = 기존 발송 기사 목록에서 같은 사건인 기사의 번호\n"
        "L = 관련 있고 새롭지만 화제성이 낮은 기사\n"
        "0 = 관련 있고 새롭고 화제성이 높은 기사\n"
        "설명, 마크다운, 따옴표 없이 판정값 하나만 출력하라.\n\n"

        f"기존 발송 기사 목록:\n{listing}\n\n"
        f"새 기사 제목:\n{title}\n"
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

        # "X", "L", "0", "2", "2번", "2." 같은 답을 모두 처리
        if answer.startswith("X"):
            return "off", None

        if answer.startswith("L"):
            return "low", None

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
    low_count = 0
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

        if verdict == "low":
            low_count += 1
            print(f"AI가 화제성이 낮다고 판단해 건너뜁니다: {item['title']}")
            log.append(f"- 💤 화제성 낮음: {label}")
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
        f"AI 중복 {ai_duplicate_count}개, 관련 없음 {off}개, 화제성 낮음 {low_count}개, "
        f"제외 언론사 {skipped}개, "
        f"{MAX_AGE_HOURS}시간 초과 {old_count}개, "
        f"발송 보류 {held}개)"
    )

    print(summary)

    if log:
        write_log([f"## {stamp} — {summary}"] + log)


if __name__ == "__main__":
    main()