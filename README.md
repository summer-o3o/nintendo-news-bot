# 📰 닌텐도 뉴스 봇

구글 뉴스에서 '닌텐도' 관련 새 기사를 찾아 디스코드 채널에 자동으로 올려주는 봇이에요.
GitHub Actions에서 돌아가서 서버 없이 **무료**로 24시간 동작해요.

## 주요 기능

- **5분마다** 구글 뉴스 '닌텐도' 검색 결과 확인
- 나온 지 **3시간 이내** 기사만 전송 (지난 기사 재등장 방지)
- 최근 **24시간** 안에 보낸 기사와 제목이 **60% 이상** 겹치면 중복으로 보고 건너뜀
- **지역 언론사** 기사 제외
- 여러 서버·채널에 동시 전송
- 기사별 전송 / 건너뜀 이유를 `log.md`에 기록

## 동작 방식

```
cron-job.org (5분마다)
   └─▶ GitHub Actions 실행 (news.yml)
         ├─ data 브랜치에서 기록(seen.json, log.md) 불러오기
         ├─ 구글 뉴스 RSS 확인 → 필터링 → 디스코드 전송
         └─ 기록을 data 브랜치에 저장
```

- `main` 브랜치: 코드만 관리
- `data` 브랜치: 봇이 자동으로 쓰는 기록 파일 (`seen.json`, `log.md`)

## 필터 순서

새 기사마다 아래 순서로 확인하고, 하나라도 걸리면 보내지 않아요.

| 순서 | 조건 | 로그 표시 |
|---|---|---|
| 1 | 이미 확인한 기사 | (기록 안 함) |
| 2 | 나온 지 3시간 초과 | ⏰ |
| 3 | 제목 키워드 불일치 (설정한 경우만) | 🔤 |
| 4 | 제외 언론사 | 🚫 |
| 5 | 최근 보낸 기사와 제목 60% 이상 겹침 | 🔁 |
| - | 통과 → 디스코드 전송 | ✅ |

### 중복 판단 방법

1. 제목에서 `[속보]`, `(종합)` 같은 괄호 내용과 기호·띄어쓰기를 지워요
2. 거의 모든 기사에 들어가는 `닌텐도`, `스위치`, `Nintendo`, `Switch`를 지워요
3. 남은 글자를 두 글자씩 쪼개서, **짧은 제목 기준으로 몇 %가 겹치는지** 계산해요
4. 최근 24시간 안에 보낸 기사 중 하나라도 60% 이상이면 중복으로 처리해요

예시
- `닌텐도, 스위치2 판매량 1000만대 돌파` vs `[속보] 닌텐도 스위치2, 판매량 1천만 대 돌파` → 78% → 중복
- `닌텐도 스위치2 가격 인상` vs `닌텐도 스위치2 신규 색상 출시` → 0% → 둘 다 전송

## 설정

`rss_to_discord.py` 위쪽에서 바꿀 수 있어요.

| 설정 | 기본값 | 설명 |
|---|---|---|
| `FEED_URL` | 닌텐도, 최근 1일 | 구글 뉴스 검색 RSS 주소 |
| `MAX_AGE_HOURS` | `3` | 이 시간보다 오래된 기사는 보내지 않음 |
| `KEYWORDS` | `""` (끔) | 제목에 이 단어가 있을 때만 전송. 예: `r"스위치\|마리오"` |
| `EXCLUDE_SOURCES` | 지역 언론사 38곳 | 받지 않을 언론사 이름 |
| `SIMILARITY` | `0.6` | 중복 판단 기준. 낮추면 더 많이 거르고, 높이면 덜 거름 |
| `DEDUP_HOURS` | `24` | 중복 비교 기간 |
| `MAX_LOG_LINES` | `2000` | `log.md` 보관 줄 수 |

## 설치 방법

### 1. 디스코드 봇 만들기

1. [Discord Developer Portal](https://discord.com/developers/applications) → 애플리케이션 생성
2. **Bot** 메뉴에서 이름·아이콘 설정, **Public Bot** 켜기, **Reset Token**으로 토큰 발급
3. **OAuth2 → URL Generator**에서 `bot` 체크 후 권한 3개만 선택
   - View Channels (채널 보기)
   - Send Messages (메시지 보내기)
   - Embed Links (링크 임베드)
4. 생성된 링크로 서버에 봇 초대

### 2. GitHub Secrets 등록

저장소 **Settings → Secrets and variables → Actions**

| 이름 | 값 |
|---|---|
| `DISCORD_BOT_TOKEN` | 봇 토큰 |
| `DISCORD_CHANNEL_IDS` | 뉴스 올릴 채널 ID (여러 개면 `111,222`) |
| `DISCORD_WEBHOOK_URL` | (선택) 웹후크로도 보낼 때 |

채널 ID는 디스코드 **설정 → 고급 → 개발자 모드**를 켠 뒤, 채널 우클릭 → **채널 ID 복사**. 채널 링크의 마지막 숫자도 채널 ID예요.

### 3. 봇 첫 로그인 (한 번만)

디스코드는 한 번도 접속한 적 없는 봇의 메시지 전송을 막아요.
**Actions → 봇 첫 로그인 (한 번만) → Run workflow**

연결 확인은 **Actions → 봇 테스트 메시지 → Run workflow**

### 4. 5분마다 실행 (cron-job.org)

GitHub 자체 예약 실행은 자주 밀려서, [cron-job.org](https://cron-job.org)에서 실행 신호를 보내요. (`news.yml`의 1시간 예약은 예비용)

1. GitHub **Fine-grained token** 발급 — 이 저장소만, 권한은 **Actions: Read and write**
2. cron-job.org에서 작업 생성
   - URL: `https://api.github.com/repos/<계정>/nintendo-news-bot/actions/workflows/news.yml/dispatches`
   - 주기: Every 5 minutes
   - Method: `POST`
   - Headers
      - `Accept: application/vnd.github+json`
      - `Authorization: Bearer <토큰>`
      - `X-GitHub-Api-Version: 2022-11-28`
   - Body: `{"ref":"main"}`
3. **TEST RUN** 결과가 `204`면 성공

## 기록 확인

- 실행 기록: `data` 브랜치의 [`log.md`](https://github.com/summer-o3o/nintendo-news-bot/blob/data/log.md) (최신이 위)
- 새 기사가 없던 실행은 기록하지 않아요

```
## 2026-10-25 07:08 — 새 기사 4개 중 1개 전송 (비슷한 기사 1개, 제외 언론사 1개, 3시간 지남 1개 건너뜀)
- ⏰ 3시간 지남: 오래된 기사 · 매체
- 🔁 중복 78%: [속보] 닌텐도 스위치2, 판매량 1천만 대 돌파 · 게임메카  ← 닌텐도, 스위치2 판매량 1000만대 돌파
- ✅ 전송: 포켓몬 신작 출시 확정 · 인벤
- 🚫 제외 언론사: 젤다 신작 · 부산일보
```

## 파일 구조

```
main
├── rss_to_discord.py              # 뉴스 확인·필터·전송
├── bot_login_once.py              # 봇 첫 로그인용
└── .github/workflows
    ├── news.yml                   # 뉴스 봇 실행
    ├── bot-login.yml              # 봇 첫 로그인 (한 번만)
    └── bot-test.yml               # 봇 테스트 메시지

data
├── seen.json                      # 확인한 기사 / 보낸 기사 제목 기록
└── log.md                         # 실행 기록
```

## 문제 해결

| 증상 | 확인할 것 |
|---|---|
| 뉴스가 안 옴 | 로그가 `새 기사 0개`면 새 기사가 없는 것 (정상) |
| `전송 실패 403` | 봇에게 해당 채널 보기·메시지 보내기 권한이 있는지 |
| `전송 실패 401` | `DISCORD_BOT_TOKEN`이 맞는지 (토큰을 재발급했다면 다시 등록) |
| cron-job.org `422` | `news.yml` 문법 오류, 또는 Body가 `{"ref":"main"}`인지 |
| 기록을 처음부터 다시 | `data` 브랜치의 `seen.json` 삭제 → 다음 실행은 기록만 하고 전송 안 함 |

## 비용

- GitHub Actions: Public 저장소는 무료
- cron-job.org: 무료
- Discord: 무료