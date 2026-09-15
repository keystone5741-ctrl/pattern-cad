"""화면용 로컬 서버. 표준 라이브러리만 쓴다.

    python -m patterncad.server            # http://127.0.0.1:8765
    python -m patterncad.server --port 9000

GET  /                → ui/index.html
GET  /ui/<file>       → ui/ 정적 파일
GET  /api/catalog     → 스타일·원형 목록
POST /api/eval        → {"kind": "style"|"block", "id", "overrides": {"body.가슴둘레": "34"}, "point_overrides": {"body.SP_F": [x, y]}}
POST /api/svg         → 같은 입력, 실물 크기 SVG 본문
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import api

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 조용히
        pass

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/catalog":
            return self._json(200, api.catalog())
        if path == "/":
            path = "/ui/index.html"
        if path.startswith("/ui/"):
            f = (UI / path[4:]).resolve()
            if UI.resolve() in f.parents and f.is_file():
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                    ctype += "; charset=utf-8"
                return self._send(200, f.read_bytes(), ctype)
        self._json(404, {"error": f"없는 주소: {path}"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            req = self._body()
            kind, ident = req.get("kind", "style"), req["id"]
            ov, po = req.get("overrides") or {}, req.get("point_overrides") or {}
            if path == "/api/eval":
                return self._json(200, api.to_json(kind, ident, ov, po))
            if path == "/api/svg":
                svg = api.to_svg(kind, ident, ov, po).encode("utf-8")
                return self._send(200, svg, "image/svg+xml; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="{ident}.svg"'})
            self._json(404, {"error": f"없는 주소: {path}"})
        except Exception as e:  # 규칙 오류를 화면에 그대로 보여 준다
            self._json(400, {"error": str(e), "trace": traceback.format_exc()})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"pattern-cad 화면: http://{a.host}:{a.port}/  (끝내기 Ctrl+C)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
