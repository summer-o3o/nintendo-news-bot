# 📰 닌텐도 뉴스 봇

닌텐도 코리아 공식 소식을 모아서 디스코드 채널에 자동으로 올려주는 봇이에요.
GitHub Actions에서 돌아가서 서버 없이 24시간 동작해요.

## 주요 기능

- **5분마다** 세 곳을 확인해요
  - 📢 [닌텐도 코리아 뉴스](https://www.nintendo.com/kr/news) 공지
  - ▶️ [한국닌텐도 공식 유튜브](https://www.youtube.com/@nintendo_kr) 새 영상
  - 🔧 Nintendo Switch 2 / Switch [본체 시스템 업데이트](https://support.nintendo.com/kr/switch2/update/index.html)
- **공식 공지**는 이미 보낸 소식과 겹치지 않으면 전부 전송 (썸네일 포함)
- **유튜브**는 중요한 영상만 전송
  - 닌텐도 다이렉트 본편은 바로 전송
  - CM·광고, 쇼츠, 다이렉트 개별 클립은 제외
  - 나머지는 신작 첫 공개, 발매 발표, 대형 서드파티 발표 같은 중요한 영상만 AI가 골라서 전송
- **시스템 업데이트**는 새 버전이 나오면 변경 내용과 함께 바로 전송
- 같은 소식은 **7일 동안 한 번만** 전송 (제목 유사도 + AI 중복 판별)
- 여러 서버·채널에 동시 전송
- 소식별 전송 / 건너뜀 이유를 `log.md`에 기록

## 동작 방식

```
cron-job.org (5분마다)
   └─▶ GitHub Actions 실행 (news.yml)
         ├─ data 브랜치에서 기록(seen.json, log.md) 불러오기
         ├─ 공식 뉴스 · 유튜브 · 시스템 업데이트 확인 → 필터링 → 디스코드 전송
         └─ 기록을 data 브랜치에 저장
```

- `main` 브랜치: 코드만 관리
- `data` 브랜치: 봇이 자동으로 쓰는 기록 파일 (`seen.json`, `log.md`)

## 필터 순서

새 소식마다 아래 순서로 확인하고, 하나라도 걸리면 보내지 않아요.

| 순서 | 조건 | 대상 | 로그 표시 |
|---|---|---|---|
| 1 | 이미 확인한 소식 | 전체 | (기록 안 함) |
| 2 | 오래된 소식 (공지: 오늘·어제 날짜만 / 영상: 24시간 / 업데이트: 7일) | 전체 | ⏰ |
| 3 | CM·광고, 쇼츠, 다이렉트 개별 클립 | 유튜브 | 📺 🎞️ 🎬 |
| 4 | 최근 7일 안에 보낸 소식과 제목 75% 이상 겹침 | 공지·유튜브 | 🔁 |
| 5 | AI가 같은 사건이라고 판단 | 공지·유튜브 | 🤖 |
| 6 | AI가 중요하지 않은 영상이라고 판단 | 유튜브 | 💤 |
| - | 통과 → 디스코드 전송 | | 📢 ▶️ 🔧 |

- 공식 공지를 영상보다 먼저 처리해서, 같은 발표면 공지가 나가고 영상은 중복으로 걸러져요.
- 다이렉트 본편과 시스템 업데이트는 5·6번 AI 판단 없이 바로 보내요.

### 중복 판단 방법

1. **제목 유사도**: 괄호 내용·기호·띄어쓰기와 `닌텐도`, `스위치`, `Nintendo`, `Switch`를 지운 뒤, 두 글자씩 쪼개서 짧은 제목 기준으로 몇 %가 겹치는지 계산해요.
2. **AI 판별**: 제목이 달라도 같은 사건인지 판단해요. 최근 7일 동안 보낸 제목 목록(최대 100개)과 비교해요.

## 디스코드 메시지

| 출처 | 머리말 | 줄 색 |
|---|---|---|
| 공식 공지 | 📢 닌텐도 공식 공지 | 노랑 |
| 유튜브 | ▶️ 한국닌텐도 유튜브 | 빨강 |
| 시스템 업데이트 | 🔧 본체 시스템 업데이트 | 하늘 |

## 설정

`rss_to_discord.py` 위쪽에서 바꿀 수 있어요.

| 설정 | 기본값 | 설명 |
|---|---|---|
| `OFFICIAL_MAX_AGE_DAYS` | `2` | 오늘·어제 날짜 공지만 전송 |
| `YOUTUBE_CHANNEL_ID` | 한국닌텐도 채널 | 확인할 유튜브 채널 |
| `YOUTUBE_MAX_AGE_HOURS` | `24` | 이 시간보다 오래된 영상은 보내지 않음 |
| `YOUTUBE_SKIP_WORDS` | `CM`, `TVCM`, `광고` | 제목에 있으면 무조건 제외 |
| `SYSTEM_UPDATE_PAGES` | Switch 2, Switch | 확인할 서포트 페이지 |
| `SIMILARITY` | `0.75` | 제목 유사도 중복 기준. 낮추면 더 많이 거름 |
| `DEDUP_HOURS` | `168` | 중복 비교 기간 (7일) |
| `AI_MODEL` | `claude-haiku-5-5` | 판단에 쓰는 AI 모델 |
| `AI_MAX_TITLES` | `100` | AI에게 보여줄 최근 발송 제목 수 |
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
| `DISCORD_CHANNEL_IDS` | 소식 올릴 채널 ID (여러 개면 `111,222`) |
| `DISCORD_WEBHOOK_URL` | (선택) 웹후크로도 보낼 때 |
| `ANTHROPIC_API_KEY` | 중복·중요도 판단용 Anthropic API 키 |

채널 ID는 디스코드 **설정 → 고급 → 개발자 모드**를 켠 뒤, 채널 우클릭 → **채널 ID 복사**. 채널 링크의 마지막 숫자도 채널 ID예요.

API 키는 [Claude Console](https://platform.claude.com)에서 발급해요. 혹시 모를 과금을 막으려면 **월 지출 한도**를 낮게(예: $2) 걸어두세요.

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

- 실행 기록: `data` 브랜치의 [`log.md`](https://github.com/summer-o3o/nintendo-news-bot/blob/data/log.md) (최신이 위, 날짜별로 묶음)
- 새 소식이 없던 실행은 기록하지 않아요
- 출처를 처음 확인할 때는 지금 있는 소식을 기록만 하고 보내지 않아요 (도배 방지)

```
## 📅 2026-10-27

**16:40** · 새 소식 5개 중 **2개 전송** · 💤 중요도 낮음 1 · 📺 규칙 제외 2

- 📢 공식 전송: 스타필드 고양 · 하남에서 「Nintendo Switch 2 게임 페스타」 개최 예정! · 닌텐도 코리아 공식
- ▶️ 유튜브 전송: Nintendo Direct 2026.10.27 · 한국닌텐도 유튜브
- 🎬 다이렉트 개별 영상: 스타폭스 [Nintendo Direct 2026.10.27] · 한국닌텐도 유튜브
- 📺 CM·광고: 젤다의 전설 시간의 오카리나 CM 오카리나편 · 한국닌텐도 유튜브
- 💤 중요도 낮음: 젤다의 전설 시간의 오카리나 소개 영상 · 한국닌텐도 유튜브
```

## 파일 구조

```
main
├── rss_to_discord.py              # 소식 확인·필터·전송
├── bot_login_once.py              # 봇 첫 로그인용
└── .github/workflows
    ├── news.yml                   # 뉴스 봇 실행
    ├── bot-login.yml              # 봇 첫 로그인 (한 번만)
    └── bot-test.yml               # 봇 테스트 메시지

data
├── seen.json                      # 확인한 소식 / 보낸 제목 기록
└── log.md                         # 실행 기록
```

## 문제 해결

| 증상 | 확인할 것 |
|---|---|
| 소식이 안 옴 | 로그가 `새 소식 0개`면 새 소식이 없는 것 (정상) |
| Actions 로그에 `○개 확인`이 0개 | 닌텐도 페이지 구조가 바뀐 것. 파싱 코드 수정 필요 |
| `시스템 버전 확인: 찾지 못함` | 서포트 페이지의 버전 표기 형식이 바뀐 것 |
| `⚠️ AI 검사 실패로 발송 보류` | `ANTHROPIC_API_KEY`, 크레딧 잔액, 월 지출 한도 확인 (다음 실행에 다시 시도) |
| `전송 실패 403` | 봇에게 해당 채널 보기·메시지 보내기 권한이 있는지 |
| `전송 실패 401` | `DISCORD_BOT_TOKEN`이 맞는지 (토큰을 재발급했다면 다시 등록) |
| cron-job.org `422` | `news.yml` 문법 오류, 또는 Body가 `{"ref":"main"}`인지 |
| 기록을 처음부터 다시 | `data` 브랜치의 `seen.json` 삭제 → 다음 실행은 기록만 하고 전송 안 함 |

## 비용

- GitHub Actions: Public 저장소는 무료
- cron-job.org: 무료
- Discord: 무료
- Anthropic API: 소식 하나 판단에 아주 적은 금액 (한 달 몇백 원 수준)
