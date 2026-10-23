# 닌텐도 뉴스 알림 (구글 뉴스 → 디스코드)

15분마다 구글 뉴스 '닌텐도' 검색 결과를 확인해서, 새 기사를 디스코드 채널에 올려요.
GitHub Actions로 돌아가서 무료이고, 컴퓨터를 켜둘 필요가 없어요.

## 설정 순서

1. **디스코드 웹후크 만들기**
   채널 설정 → 연동 → 웹후크 → 새 웹후크
   여기서 이름과 프로필 사진을 원하는 대로 바꾸고 **웹후크 URL 복사**

2. **GitHub 저장소 만들기**
   github.com 가입 → New repository → 이름 아무거나 → Create
   이 폴더의 파일들을 그대로 업로드 (`.github/workflows/news.yml` 경로 유지)

3. **웹후크 URL 등록**
   저장소 Settings → Secrets and variables → Actions → New repository secret
   - Name: `DISCORD_WEBHOOK_URL`
   - Secret: 복사한 웹후크 URL

4. **실행 확인**
   Actions 탭 → "닌텐도 뉴스 알림" → Run workflow
   첫 실행은 기존 기사를 기록만 하고 보내지 않아요. 그 뒤로 새 기사부터 올라와요.

## 바꾸고 싶을 때

- 검색어: `rss_to_discord.py`의 `FEED_URL`에서 `q=` 뒤를 바꾸기
- 특정 단어만 받기: `KEYWORDS = r"스위치|마리오"`처럼 넣기
- 확인 주기: `news.yml`의 `*/15`를 `*/30` 등으로

## 참고

- 구글 뉴스 RSS에는 기사 이미지가 없어서 제목·언론사·시간만 올라가요.
- 웹후크 URL은 비밀번호처럼 다뤄주세요. 공개되면 누구나 그 채널에 글을 쓸 수 있어요.
