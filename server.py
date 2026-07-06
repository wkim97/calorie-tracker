#!/usr/bin/env python3
"""칼로리 트래커 로컬 서버.

이 디렉토리의 index.html을 서빙하고, 기록을 data.json 파일에 저장/불러온다.
실행: python3 server.py  (또는 start.command 더블클릭)
"""
import json
import os
import urllib.error
import urllib.request
from http.server import HTTPServer, SimpleHTTPRequestHandler

DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(DIR, "data.json")
CONFIG_FILE = os.path.join(DIR, "config.json")
PORT = 8787
URL = f"http://127.0.0.1:{PORT}"

AI_SYSTEM_PROMPT = (
    "너는 음식 영양 정보 추정 도우미다. 음식 이름 또는 사진을 보고 "
    "일반적인 1인분(1개/1팩/1그릇) 기준 영양 정보를 추정해라. "
    "브랜드 제품명이 명확하면 실제 제품 영양성분표 기준으로 답해라. "
    "사용자 코멘트가 있으면 실제 섭취량을 그에 맞게 조정해서 계산해라 "
    "(예: '밥은 반만 먹음' → 밥 분량 절반만 합산, '또띠야는 안 먹음' → 제외, "
    "'계란 하나 추가' → 추가분 합산). 코멘트가 사진과 다르면 코멘트가 우선이다. "
    "반드시 아래 JSON 형식으로만 응답해라:\n"
    '{"name": "간결한 한국어 음식 이름", "kcal": 숫자, "protein": 숫자(g), '
    '"carbs": 숫자(g), "fat": 숫자(g), "note": "기준량·코멘트 반영 내용 한 줄"}'
)


def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def call_openai(name, image, comment=None):
    config = load_config()
    api_key = config.get("openai_api_key")
    if not api_key:
        return {"error": "config.json에 openai_api_key가 없습니다"}

    lines = []
    if name:
        lines.append(f"음식: {name}")
    if comment:
        lines.append(f"사용자 코멘트(실제 섭취량에 반영할 것): {comment}")
    if not lines:
        lines.append("사진 속 음식의 영양 정보를 추정해줘.")
    content = [{"type": "text", "text": "\n".join(lines)}]
    if image:
        content.append({"type": "image_url", "image_url": {"url": image}})

    payload = {
        "model": config.get("model", "gpt-5.4"),
        "messages": [
            {"role": "system", "content": AI_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.load(resp)
        return json.loads(result["choices"][0]["message"]["content"])
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        return {"error": f"OpenAI API 오류 ({e.code}): {detail}"}
    except Exception as e:
        return {"error": f"AI 호출 실패: {e}"}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)

    def do_GET(self):
        if self.path == "/config.json":  # API 키가 든 파일은 서빙하지 않음
            self.send_response(403)
            self.end_headers()
            return
        if self.path == "/data":
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "rb") as f:
                    body = f.read()
            else:
                body = b"null"
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/ai":
            length = int(self.headers.get("Content-Length", 0))
            try:
                req = json.loads(self.rfile.read(length))
            except ValueError:
                req = {}
            result = call_openai(req.get("name"), req.get("image"), req.get("comment"))
            body = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != "/data":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            json.loads(body)
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        # 임시 파일에 쓴 뒤 교체 — 저장 도중 꺼져도 data.json이 깨지지 않게
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "wb") as f:
            f.write(body)
        os.replace(tmp, DATA_FILE)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass  # 요청 로그 끄기


def lan_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def main():
    try:
        # 0.0.0.0 — 폰(Tailscale/같은 Wi-Fi)에서도 접속할 수 있게 모든 인터페이스에 연다
        server = HTTPServer(("0.0.0.0", PORT), Handler)
    except OSError:
        print(f"이미 실행 중입니다 → {URL}")
        return
    print(f"칼로리 트래커 실행 중 → {URL}")
    ip = lan_ip()
    if ip:
        print(f"같은 Wi-Fi의 폰에서: http://{ip}:{PORT}")
    print(f"Tailscale 연결 후 폰에서: http://<맥의 Tailscale IP>:{PORT}")
    print(f"기록 파일: {DATA_FILE}")
    print("종료: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")


if __name__ == "__main__":
    main()
