"""봇을 디스코드에 한 번 접속시키는 스크립트 (처음 한 번만 실행)."""
import json
import os
import sys

import websocket

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
if not TOKEN:
    sys.exit("DISCORD_BOT_TOKEN이 설정되지 않았어요.")

ws = websocket.create_connection("wss://gateway.discord.gg/?v=10&encoding=json", timeout=30)
hello = json.loads(ws.recv())
ws.send(json.dumps({"op": 2, "d": {
    "token": TOKEN, "intents": 0,
    "properties": {"os": "linux", "browser": "news-bot", "device": "news-bot"}}}))
while True:
    msg = json.loads(ws.recv())
    if msg.get("t") == "READY":
        print(f"로그인 성공: {msg['d']['user']['username']}")
        break
    if msg.get("op") == 9:
        sys.exit("로그인 실패: 토큰이 틀렸어요.")
ws.close()
