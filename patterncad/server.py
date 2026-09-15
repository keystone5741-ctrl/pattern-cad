"""화면용 로컬 서버. 표준 라이브러리만 쓴다.

    python -m patterncad.server            # http://127.0.0.1:8765
    python -m patterncad.server --port 9000

GET  /                → ui/index.html
GET  /ui/<file>       → ui/ 정적 파일
GET  /api/catalog     → 스타일·원형 목록
POST /api/eval        → {"kind": "style"|"block", "id", "overrides": {"body.가슴둘레": "34"},
                          "point_overrides": {"body.SP_F": [x, y]}, "line_overrides": {"body.앞암홀": {"0": {"c1": [비율, 각]}}}}
POST /api/svg         → 같은 입력, 실물 크기 SVG 본문
POST /api/pieces      → 같은 입력 + "piece_settings" → 조각(완성선·재단선·노치·식서)
POST /api/dxf         → 같은 입력 (+ "grading": {"system", "base", "sizes"}), AAMA 층 DXF (인치)
GET  /api/sizes · POST /api/grade  → 사이즈 체계 / 사이즈별 선 (치수 재대입 그레이딩)
POST /api/marker_dxf  → 같은 입력 + "placements", "width" → 마카 DXF (놓인 자리대로, 한 층)
GET  /api/projects · GET/POST /api/project?name=   → 프로젝트 파일 (projects/*.pcad)
POST /api/overlay     → {"block": 원형id, "piece": 조각} → 원본 도면 맞춤 변환 (verify/fits.json 에 캐시)
GET  /api/page?page=48&layers=pattern,developed    → 추출 도면의 층 그림 (SVG 조각)
GET  /api/wizard · POST /api/wizard                → 마법사 재료 / 선택 → 치수 덮어쓰기
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import api, wizard

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
        q = dict(p.split("=", 1) for p in urlparse(self.path).query.split("&") if "=" in p)
        from urllib.parse import unquote  # noqa: PLC0415
        q = {k: unquote(v) for k, v in q.items()}
        try:
            if path == "/api/catalog":
                return self._json(200, api.catalog())
            if path == "/api/wizard":
                return self._json(200, wizard.catalog())
            if path == "/api/sizes":
                return self._json(200, api.size_systems())
            if path == "/api/projects":
                return self._json(200, api.project_list())
            if path == "/api/project":
                return self._json(200, api.project_load(q.get("name", "")))
            if path == "/api/page":
                layers = tuple((q.get("layers") or "pattern,developed").split(","))
                body = api.page_svg_layers(int(q["page"]), layers).encode("utf-8")
                return self._send(200, body, "image/svg+xml; charset=utf-8")
        except Exception as e:  # noqa: BLE001
            return self._json(400, {"error": str(e)})
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
            if path == "/api/project":
                return self._json(200, api.project_save(req.get("name", ""), req))
            if path == "/api/overlay":
                return self._json(200, api.overlay_fit(req["block"], req.get("piece") or None, bool(req.get("refresh"))))
            if path == "/api/wizard":
                ov = wizard.overrides_for(req["style"], req.get("body") or {}, req.get("fit"))
                return self._json(200, {"kind": "style", "id": req["style"], "overrides": ov})
            kind, ident = req.get("kind", "style"), req["id"]
            ov, po, lo = req.get("overrides") or {}, req.get("point_overrides") or {}, req.get("line_overrides") or {}
            ps = req.get("piece_settings") or {}
            if path == "/api/pieces":
                return self._json(200, api.pieces_json(kind, ident, ov, po, lo, ps))
            if path == "/api/marker_dxf":
                body = api.marker_dxf(kind, ident, ov, po, lo, ps, req.get("placements") or [],
                                      float(req.get("width") or 58), req.get("grading")).encode("utf-8")
                return self._send(200, body, "application/dxf; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="{ident}_marker.dxf"'})
            if path == "/api/grade":
                g = req.get("grading") or {}
                return self._json(200, api.grade_json(kind, ident, ov, po, lo, g["system"], g["base"], g.get("sizes") or []))
            if path == "/api/dxf":
                body = api.to_dxf(kind, ident, ov, po, lo, ps, req.get("grading")).encode("utf-8")
                return self._send(200, body, "application/dxf; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="{ident}.dxf"'})
            if path == "/api/eval":
                return self._json(200, api.to_json(kind, ident, ov, po, lo))
            if path == "/api/svg":
                svg = api.to_svg(kind, ident, ov, po, lo).encode("utf-8")
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
