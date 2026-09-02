"""포트폴리오 PDF(reference/portfolio.pdf)에서 패턴 제도 선·치수·유의사항을 분리해 정리한다.

일러스트레이터에서 내보낸 벡터 PDF라 선과 글자가 그대로 뽑힌다. 페이지 레이아웃이
전권에 걸쳐 같은 템플릿(제목 → 사이즈표 → 도식화 박스 → 패턴 제도 → 유의사항 →
전개/그레이딩/마카)이라, 섹션 헤더의 y 좌표와 테두리 박스로 영역을 나눈 뒤
선마다 어느 영역에 속하는지로 분류한다.

    python tools/extract_portfolio.py            # extracted/ 에 전부 생성
    python tools/extract_portfolio.py --pages 4 44 95   # 일부만 (디버그)

결과 (extracted/<카테고리>/<번호_아이템>/):
    pNNN.svg          페이지 전체를 층(layer)별 그룹으로 나눈 SVG
    pNNN_pattern.svg  패턴 선 + 치수 글자만
    pNNN_pattern.png  위 SVG 미리보기
    size.json         신체 사이즈 / 패턴 사이즈 표
    annotations.json  패턴 위 치수·공식 글자와 좌표
    notes.md          패턴 제도 유의사항 본문
좌표는 PDF 포인트(pt) 그대로다. 1 pt = 25.4/72 mm. 도면은 실물 축척이 아니므로
실제 치수는 좌표가 아니라 annotations의 글자에서 읽어야 한다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "reference" / "portfolio.pdf"
OUT = ROOT / "extracted"

PT_TO_MM = 25.4 / 72

# ---------------------------------------------------------------- 페이지 → 카테고리
CATEGORY_RANGES = [
    (1, 26, "skirt", "스커트"),
    (27, 42, "pants", "팬츠"),
    (43, 72, "top", "상의"),
    (73, 100, "jacket", "자켓"),
]

# 큰 제목 없이 시작하는 원형 페이지의 슬러그 (한글 제목 → ASCII 폴더명)
SPECIAL_TITLES = {
    "시추니 기본 원형": ("00a_sichuni_basic_block", "시추니 기본 원형", "Sichuni Basic Block"),
    "시추니 무다트 원형": ("00b_sichuni_dartless_block", "시추니 무다트 원형", "Sichuni Dartless Block"),
}

# 섹션 헤더(16~18pt) 텍스트 → 영역 이름. 이 순서로 페이지가 위→아래 진행된다.
SECTION_HEADERS = {
    "패턴 제도 Pattern": "pattern",
    "패턴 제도 유의사항": "notes",
    "전개 패턴 확인": "developed",
    "그레이딩 grading": "grading",
    "마카 maka": "marker",
    "디지타이저 입력 digt": "digitize",
}
FLAT_HEADER = "도식화 Flat"

# 선 층 이름 (SVG 그룹 id 겸 pattern-only 선택 기준)
PATTERN_LAYERS = {"pattern", "developed"}

# ---------------------------------------------------------------- 색 판별
def _near(c, target, tol=0.06):
    return c is not None and all(abs(a - b) <= tol for a, b in zip(c, target))


def is_dark(c):
    return c is not None and max(c) < 0.3


def is_brown(c):  # 헤더 알약·테두리 박스 색
    return _near(c, (0.46, 0.38, 0.35), 0.08) or _near(c, (0.53, 0.43, 0.41), 0.08)


def is_note_gray(c):  # 유의사항 회색 바탕
    return _near(c, (0.90, 0.88, 0.87), 0.04)


def is_grading_guide(c):  # 그레이딩 축 (빨강/노랑/청록)
    return (
        _near(c, (0.93, 0.18, 0.17), 0.12)
        or _near(c, (0.93, 0.91, 0.31), 0.12)
        or _near(c, (0.28, 0.76, 0.80), 0.12)
    )


def is_white(c):
    return _near(c, (1, 1, 1), 0.03)


# ---------------------------------------------------------------- 인치 표기 해석
# 이 포트폴리오의 표기: 3.1/2” = 3½", 1/8” = ⅛", 26” = 26", 5“~6” = 범위
INCH_QUOTES = "”\"″“"
_MIXED = re.compile(r"(?<![\d/])(\d+)\.(\d+)/(\d+)(?![\d/])")
_FRAC = re.compile(r"(?<![\d.])(\d+)/(\d+)(?![\d/])")
_NUM = re.compile(r"(?<![\d./])(\d+(?:\.\d+)?)(?![\d/])")
_HAS_VAR = re.compile(r"[A-Z]\s*/\s*\d|[A-Z]\.[A-Z]|[+\-×x]\s*\w|ease|dart|여유|다트", re.I)


def parse_inch(text: str):
    """'3.1/2”' → 3.5, '1/8”' → 0.125, '26”' → 26.0. 못 읽으면 None."""
    t = text.strip()
    m = _MIXED.fullmatch(t.rstrip(INCH_QUOTES).strip())
    if m:
        return int(m[1]) + int(m[2]) / int(m[3])
    m = _FRAC.fullmatch(t.rstrip(INCH_QUOTES).strip())
    if m:
        return int(m[1]) / int(m[2])
    m = _NUM.fullmatch(t.rstrip(INCH_QUOTES).strip())
    if m:
        return float(m[1])
    return None


def classify_text(text: str) -> str:
    """number | formula | label"""
    if parse_inch(text) is not None:
        return "number"
    if _HAS_VAR.search(text) or any(q in text for q in INCH_QUOTES):
        return "formula"
    return "label"


# ---------------------------------------------------------------- 자료구조
@dataclass
class Span:
    text: str
    size: float
    font: str
    bbox: tuple  # x0,y0,x1,y1
    origin: tuple  # baseline x,y
    layer: str = ""
    block: int = -1  # pymupdf 텍스트 블록 번호 (문단 단위 읽기 순서 복원용)

    @property
    def cx(self):
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def cy(self):
        return (self.bbox[1] + self.bbox[3]) / 2


@dataclass
class Item:
    slug: str
    num: int | None
    title_en: str
    title_ko: str
    category: str
    category_ko: str
    pages: list = field(default_factory=list)


# ---------------------------------------------------------------- 텍스트 수집
def page_spans(page) -> list[Span]:
    out = []
    for bi, b in enumerate(page.get_text("dict")["blocks"]):
        if b["type"] != 0:
            continue
        for l in b["lines"]:
            for s in l["spans"]:
                if not s["text"].strip():
                    continue
                out.append(
                    Span(s["text"], round(s["size"], 1), s["font"], tuple(s["bbox"]), tuple(s["origin"]), block=bi)
                )
    return out


def page_lines(page) -> list[tuple[str, float, tuple]]:
    """(줄 텍스트, 최대 글자크기, bbox) — 헤더/제목은 여러 span으로 쪼개져 있어 줄 단위로 본다."""
    out = []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        for l in b["lines"]:
            txt = "".join(s["text"] for s in l["spans"]).strip()
            if not txt:
                continue
            out.append((txt, max(s["size"] for s in l["spans"]), tuple(l["bbox"])))
    return out


TITLE_RE = re.compile(r"^(\d+)\.\s*(.+?)\s*(?:\(([^)]*)\))?\s*$")


def find_title(lines):
    """28pt 이상 줄들을 y 기준으로 묶어 제목 하나로 합친다. '1.' 과 'Tailored Jacket' 처럼 나뉜 경우 처리."""
    big = [(t, s, bb) for t, s, bb in lines if s >= 28]
    if not big:
        return None
    big.sort(key=lambda x: (round(x[2][1] / 10), x[2][0]))
    return " ".join(t for t, _, _ in big).replace("  ", " ").strip()


# ---------------------------------------------------------------- 영역 판정
@dataclass
class Regions:
    bands: list  # [(y0, y1, name)]
    flat_boxes: list  # rects
    note_boxes: list  # rects
    size_labels: list  # Span (신체/패턴 사이즈)
    has_title: bool
    title_rect: pymupdf.Rect | None = None

    def band_of(self, y):
        for y0, y1, name in self.bands:
            if y0 <= y < y1:
                return name
        return self.bands[-1][2] if self.bands else "pattern"

    def layer_for_point(self, x, y) -> str:
        for r in self.flat_boxes:
            if r.contains(pymupdf.Point(x, y)):
                return "flat"
        for r in self.note_boxes:
            if r.contains(pymupdf.Point(x, y)):
                return "notes"
        return self.band_of(y)


def detect_regions(page, spans, lines, has_title, prev_last_band="pattern") -> Regions:
    H = page.rect.height
    headers = []
    flat_header = None
    title_rect = None
    # 헤더는 span 단위로 본다 — 같은 줄 높이에 다른 글자가 있으면 줄 텍스트가 합쳐져 못 찾는다
    for sp in spans:
        t = sp.text.strip()
        if 15.5 <= sp.size <= 18.5:
            if t in SECTION_HEADERS:
                headers.append((sp.bbox[1], SECTION_HEADERS[t]))
            elif t == FLAT_HEADER:
                flat_header = sp.bbox
    for t, s, bb in lines:
        if s >= 28:
            title_rect = pymupdf.Rect(bb) if title_rect is None else title_rect | pymupdf.Rect(bb)
    headers.sort()

    # 첫 헤더 위쪽 영역: 제목 페이지면 (사이즈표 +) 패턴 제도, 아니면 이전 페이지 마지막 영역의 연속
    bands = []
    first_y = headers[0][0] if headers else H
    bands.append((0, first_y, "pattern" if has_title else prev_last_band))
    for i, (y, name) in enumerate(headers):
        y_next = headers[i + 1][0] if i + 1 < len(headers) else H
        bands.append((y, y_next, name))

    def band_of(y):
        for y0, y1, name in bands:
            if y0 <= y < y1:
                return name
        return bands[-1][2]

    flat_boxes, note_boxes = [], []
    for d in page.get_drawings():
        r = d["rect"]
        if r.width < 80 or r.height < 40:
            continue
        c, f, w = d.get("color"), d.get("fill"), d.get("width") or 0
        if f is not None and is_note_gray(f):
            # 그레이딩·마카·전개 섹션도 같은 회색 바탕을 깔아 두므로, 그 헤더 아래 박스는 유의사항이 아니다
            if band_of(r.y0 + 3) not in ("grading", "marker", "developed", "digitize"):
                note_boxes.append(r)
        elif c is not None and is_brown(c) and w >= 0.9 and flat_header is not None:
            # 헤더 알약이 박스 상단 테두리에 걸쳐 있거나 바로 위에 붙어 있다
            fh = pymupdf.Rect(flat_header) + (-5, -5, 5, 14)
            if r.intersects(fh) and r.width < 0.7 * page.rect.width:
                flat_boxes.append(r)
    if not flat_boxes:
        # 테두리 박스 없이 도식화만 놓인 페이지: 'Front'/'Back' 라벨 위쪽을 도식화 영역으로 잡는다
        fb = [sp for sp in spans if sp.text.strip() in ("Front", "Back") and 10 <= sp.size <= 14
              and band_of(sp.cy) == bands[0][2]]
        if fb:
            x0 = min(sp.bbox[0] for sp in fb) - 70
            x1 = max(sp.bbox[2] for sp in fb) + 70
            y0 = 0  # 제목 옆 여백까지 포함 (도식화가 제목 높이까지 올라온 페이지가 있다)
            y1 = max(sp.bbox[3] for sp in fb) + 2
            flat_boxes.append(pymupdf.Rect(x0, y0, x1, y1))
    size_labels = [s for s in spans if s.text.strip() in ("신체 사이즈", "패턴 사이즈")]
    return Regions(bands, flat_boxes, note_boxes, size_labels, has_title, title_rect)


# ---------------------------------------------------------------- 선 분류
def classify_drawing(d, regions: Regions) -> str | None:
    """층 이름을 돌려준다. None 이면 버린다."""
    c, f, w = d.get("color"), d.get("fill"), d.get("width") or 0
    r = d["rect"]
    cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2

    if c is not None and is_white(c) and (f is None or is_white(f)):
        return None  # 흰 선/배경
    if f is not None and c is None:
        # 채움만 있는 도형: 큰 것은 장식(박스·바탕), 작은 짙은 것은 화살촉·점
        if is_note_gray(f) or is_brown(f) or is_white(f):
            return "decoration"
        if is_dark(f) and r.width < 10 and r.height < 10:
            return regions.layer_for_point(cx, cy)
        return "decoration"
    if c is not None and is_grading_guide(c):
        return "grading_guide"
    if c is not None and is_brown(c):
        return "decoration"
    if f is not None and not is_white(f) and not is_dark(f) and r.width > 20:
        return "decoration"  # 색 채움 강조 원 등
    return regions.layer_for_point(cx, cy)


# ---------------------------------------------------------------- SVG 출력
def _fmt(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def drawing_to_path(d) -> str:
    parts = []
    cur = None
    for it in d["items"]:
        kind = it[0]
        if kind == "l":
            p1, p2 = it[1], it[2]
            if cur is None or abs(cur.x - p1.x) > 0.01 or abs(cur.y - p1.y) > 0.01:
                parts.append(f"M{_fmt(p1.x)} {_fmt(p1.y)}")
            parts.append(f"L{_fmt(p2.x)} {_fmt(p2.y)}")
            cur = p2
        elif kind == "c":
            p1, p2, p3, p4 = it[1], it[2], it[3], it[4]
            if cur is None or abs(cur.x - p1.x) > 0.01 or abs(cur.y - p1.y) > 0.01:
                parts.append(f"M{_fmt(p1.x)} {_fmt(p1.y)}")
            parts.append(f"C{_fmt(p2.x)} {_fmt(p2.y)} {_fmt(p3.x)} {_fmt(p3.y)} {_fmt(p4.x)} {_fmt(p4.y)}")
            cur = p4
        elif kind == "re":
            r = it[1]
            parts.append(f"M{_fmt(r.x0)} {_fmt(r.y0)}H{_fmt(r.x1)}V{_fmt(r.y1)}H{_fmt(r.x0)}Z")
            cur = None
        elif kind == "qu":
            q = it[1]
            pts = [q.ul, q.ur, q.lr, q.ll]
            parts.append("M" + "L".join(f"{_fmt(p.x)} {_fmt(p.y)}" for p in pts) + "Z")
            cur = None
    if d.get("closePath") and parts and not parts[-1].endswith("Z"):
        parts.append("Z")
    return "".join(parts)


def _rgb(c):
    return "rgb(%d,%d,%d)" % tuple(int(round(v * 255)) for v in c)


def drawing_to_svg(d) -> str:
    c, f, w = d.get("color"), d.get("fill"), d.get("width")
    attrs = [f'd="{drawing_to_path(d)}"']
    attrs.append(f'stroke="{_rgb(c)}"' if c is not None else 'stroke="none"')
    attrs.append(f'fill="{_rgb(f)}"' if f is not None else 'fill="none"')
    if c is not None:
        attrs.append(f'stroke-width="{_fmt(w or 0.5)}"')
        dash = d.get("dashes")
        if dash and dash != "[] 0":
            m = re.match(r"\[([\d.\s]+)\]\s*([\d.]+)", dash)
            if m and m[1].strip():
                attrs.append(f'stroke-dasharray="{m[1].strip().replace(" ", ",")}"')
        attrs.append('stroke-linecap="round" stroke-linejoin="round"')
    return f"<path {' '.join(attrs)}/>"


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def span_to_svg(s: Span) -> str:
    return (
        f'<text x="{_fmt(s.origin[0])}" y="{_fmt(s.origin[1])}" font-size="{_fmt(s.size)}">'
        f"{_esc(s.text)}</text>"
    )


LAYER_ORDER = [
    "decoration", "size", "flat", "notes", "grading_guide", "grading", "marker", "digitize",
    "developed", "pattern",
]
LAYER_LABELS = {
    "decoration": "장식(헤더·박스·바탕)", "size": "사이즈표", "flat": "도식화", "notes": "유의사항 그림",
    "grading_guide": "그레이딩 축", "grading": "그레이딩", "marker": "마카", "digitize": "디지타이저",
    "developed": "전개 패턴", "pattern": "패턴 제도",
}


def write_svg(path: Path, page_rect, layers: dict, text_layers: dict, only: set | None = None):
    W, H = page_rect.width, page_rect.height
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        f'viewBox="0 0 {_fmt(W)} {_fmt(H)}" width="{_fmt(W * PT_TO_MM)}mm" height="{_fmt(H * PT_TO_MM)}mm" '
        f'font-family="Apple SD Gothic Neo, Malgun Gothic, sans-serif">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]
    for name in LAYER_ORDER:
        if only is not None and name not in only:
            continue
        paths = layers.get(name, [])
        texts = text_layers.get(name, [])
        if not paths and not texts:
            continue
        out.append(f'<g id="{name}" inkscape:groupmode="layer" inkscape:label="{LAYER_LABELS.get(name, name)}">')
        out.extend(drawing_to_svg(d) for d in paths)
        if texts:
            out.append('<g id="%s_text" fill="rgb(40,30,25)">' % name)
            out.extend(span_to_svg(s) for s in texts)
            out.append("</g>")
        out.append("</g>")
    out.append("</svg>")
    path.write_text("\n".join(out), encoding="utf-8")


def svg_to_png(svg_path: Path, png_path: Path, dpi=110):
    doc = pymupdf.open(str(svg_path))
    doc[0].get_pixmap(dpi=dpi).save(str(png_path))
    doc.close()


# ---------------------------------------------------------------- 사이즈표
def extract_size_table(spans: list[Span], regions: Regions):
    """(표 dict, 표에 쓰인 span 목록). 표가 없으면 (None, [])."""
    labels = {s.text.strip(): s for s in regions.size_labels}
    if "신체 사이즈" not in labels or "패턴 사이즈" not in labels:
        return None, []
    a, b = labels["신체 사이즈"], labels["패턴 사이즈"]
    small = [s for s in spans if s.size <= 10 and s not in (a, b)]

    vertical_layout = abs(a.cy - b.cy) < 4  # 두 라벨이 같은 줄 → 세로표(항목이 행)
    if vertical_layout:
        x_min, x_max = min(a.bbox[0], b.bbox[0]) - 80, max(a.bbox[2], b.bbox[2]) + 8
        y_min, y_max = a.bbox[1] - 4, a.bbox[3] + 260
        cells = [s for s in small if x_min <= s.cx <= x_max and y_min <= s.cy <= y_max]
        # 행 = y 묶음, 각 행: 왼쪽 라벨, a 열 값, b 열 값
        rows = _cluster(cells, key=lambda s: s.cy, tol=4)
        body, pattern, used = {}, {}, []
        for row in rows:
            row.sort(key=lambda s: s.cx)
            name = [s for s in row if s.cx < a.bbox[0] - 2]
            if not name:
                continue
            key = " ".join(s.text.strip() for s in name)
            va = [s for s in row if a.bbox[0] - 6 <= s.cx <= a.bbox[2] + 6]
            vb = [s for s in row if b.bbox[0] - 6 <= s.cx <= b.bbox[2] + 6]
            body[key] = " ".join(s.text.strip() for s in va) or None
            pattern[key] = " ".join(s.text.strip() for s in vb) or None
            used += name + va + vb
    else:
        x_min, x_max = a.bbox[0] - 4, a.bbox[0] + 420
        y_min, y_max = a.bbox[1] - 26, max(a.bbox[3], b.bbox[3]) + 4
        cells = [s for s in small if x_min <= s.cx <= x_max and y_min <= s.cy <= y_max]
        header = [s for s in cells if s.cy < a.bbox[1] - 2]
        header.sort(key=lambda s: s.cx)
        cols = [(s.cx, s.text.strip()) for s in header]
        used = list(header)

        def row_values(label):
            vals = [s for s in cells if abs(s.cy - label.cy) < 4 and s.cx > label.bbox[2]]
            out = {}
            for s in vals:
                # 가장 가까운 열 헤더에 붙인다
                if not cols:
                    continue
                name = min(cols, key=lambda c: abs(c[0] - s.cx))[1]
                out[name] = (out.get(name, "") + " " + s.text.strip()).strip()
                used.append(s)
            return out

        body, pattern = row_values(a), row_values(b)
    keys = list(dict.fromkeys(list(body) + list(pattern)))
    return {
        "unit": "inch",
        "measurements": [
            {
                "name": k,
                "body": body.get(k),
                "body_in": parse_inch(body[k]) if body.get(k) else None,
                "pattern": pattern.get(k),
                "pattern_in": parse_inch(pattern[k]) if pattern.get(k) else None,
            }
            for k in keys
        ],
    }, used


def _cluster(items, key, tol):
    items = sorted(items, key=key)
    groups, cur = [], []
    for it in items:
        if cur and abs(key(it) - key(cur[-1])) > tol:
            groups.append(cur)
            cur = []
        cur.append(it)
    if cur:
        groups.append(cur)
    return groups


# ---------------------------------------------------------------- 유의사항 본문
def notes_markdown(page_no: int, spans: list[Span], regions: "Regions", page_width: float) -> str:
    """유의사항 층 글자를 문단(블록) 단위로, 회색 박스 → 2단 구성을 살려 읽기 순서로 재구성한다.
    8pt 미만은 설명 그림 안의 치수 라벨이라 본문에서 뺀다."""
    notes = [s for s in spans if s.layer == "notes" and s.text.strip() not in SECTION_HEADERS and s.size >= 8]
    if not notes:
        return ""

    # 블록(문단) 단위로 묶고 bbox 계산
    blocks: dict[int, list[Span]] = defaultdict(list)
    for s in notes:
        blocks[s.block].append(s)
    paras = []
    for bi, ss in blocks.items():
        x0 = min(s.bbox[0] for s in ss); y0 = min(s.bbox[1] for s in ss)
        x1 = max(s.bbox[2] for s in ss); y1 = max(s.bbox[3] for s in ss)
        box_i = next((i for i, r in enumerate(regions.note_boxes) if r.contains(pymupdf.Point((x0 + x1) / 2, (y0 + y1) / 2))), -1)
        paras.append({"spans": ss, "rect": (x0, y0, x1, y1), "box": box_i})

    # 그룹: 회색 박스별(박스 y 순), 박스 밖은 별도 그룹으로 y 순
    def group_key(p):
        if p["box"] >= 0:
            return (regions.note_boxes[p["box"]].y0, p["box"])
        return (p["rect"][1], -1)

    groups: dict = defaultdict(list)
    for p in paras:
        groups[p["box"]].append(p)
    ordered_groups = sorted(groups.values(), key=lambda g: min(group_key(p) for p in g))

    mid = page_width / 2
    out = [f"\n<!-- p{page_no:03d} -->"]
    for g in ordered_groups:
        g.sort(key=lambda p: (p["rect"][1], p["rect"][0]))
        # 전폭 문단이 나오면 구간을 끊고, 구간 안에서는 왼쪽 단 → 오른쪽 단 순으로 읽는다
        segment: list = []

        def flush():
            left = [p for p in segment if p["rect"][2] < mid + 20]
            right = [p for p in segment if p not in left]
            for p in sorted(left, key=lambda p: p["rect"][1]) + sorted(right, key=lambda p: p["rect"][1]):
                out.append(para_text(p["spans"]))
            segment.clear()

        for p in g:
            full = (p["rect"][2] - p["rect"][0]) > 0.55 * page_width
            if full:
                flush()
                out.append(para_text(p["spans"]))
            else:
                segment.append(p)
        flush()
        out.append("")
    return "\n".join(out) + "\n"


def para_text(ss: list[Span]) -> str:
    lines = _cluster(ss, key=lambda s: s.cy, tol=3)
    parts = []
    for ln in lines:
        ln.sort(key=lambda s: s.bbox[0])
        parts.append(" ".join(s.text.strip() for s in ln).strip())
    txt = " ".join(parts)
    size = max(s.size for s in ss)
    if size >= 15.5:
        return f"\n## {txt}\n"
    if size >= 11.5:
        return f"\n### {txt}\n"
    return txt + "\n"


# ---------------------------------------------------------------- 메인 처리
def build_index(doc) -> list[Item]:
    items: list[Item] = []
    cur: Item | None = None
    for pno in range(1, doc.page_count + 1):
        page = doc[pno - 1]
        cat = next((c for lo, hi, c, _ in CATEGORY_RANGES if lo <= pno <= hi), "misc")
        cat_ko = next((k for lo, hi, c, k in CATEGORY_RANGES if lo <= pno <= hi), "")
        title = find_title(page_lines(page))
        if title in ("TOP", "JACKET") or title is None and pno in (1, 2, 3, 27):
            title = None  # 카테고리 표지
            cur = None
        if title:
            if title in SPECIAL_TITLES:
                slug, ko, en = SPECIAL_TITLES[title]
                cur = Item(slug, None, en, ko, cat, cat_ko)
            else:
                m = TITLE_RE.match(title)
                if m:
                    num, en, ko = int(m[1]), m[2].strip(), (m[3] or "").strip()
                    en_slug = re.sub(r"[^a-z0-9]+", "_", en.lower()).strip("_")
                    cur = Item(f"{num:02d}_{en_slug}", num, en, ko, cat, cat_ko)
                else:
                    cur = None
            if cur:
                items.append(cur)
        if cur:
            cur.pages.append(pno)
    return items


def process_page(doc, pno: int, item_dir: Path, item: Item, previews: bool = True, prev_last_band="pattern") -> dict:
    page = doc[pno - 1]
    spans = page_spans(page)
    lines = page_lines(page)
    has_title = find_title(lines) is not None
    regions = detect_regions(page, spans, lines, has_title, prev_last_band)
    size_table, size_cells = extract_size_table(spans, regions) if has_title else (None, [])
    size_ids = {id(s) for s in size_cells} | {id(s) for s in regions.size_labels}

    layers: dict[str, list] = defaultdict(list)
    for d in page.get_drawings():
        layer = classify_drawing(d, regions)
        if layer:
            layers[layer].append(d)

    text_layers: dict[str, list] = defaultdict(list)
    for s in spans:
        t = s.text.strip()
        in_title = regions.title_rect is not None and regions.title_rect.y0 <= s.cy <= regions.title_rect.y1
        if s.size >= 28 or t in SECTION_HEADERS or t == FLAT_HEADER or in_title:
            s.layer = "decoration"
        elif id(s) in size_ids:
            s.layer = "size"
        else:
            s.layer = regions.layer_for_point(s.cx, s.cy)
        text_layers[s.layer].append(s)

    stem = f"p{pno:03d}"
    write_svg(item_dir / f"{stem}.svg", page.rect, layers, text_layers)
    pattern_layers = PATTERN_LAYERS | {"grading", "grading_guide", "marker"}
    has_pattern = any(layers.get(l) for l in pattern_layers)
    if has_pattern:
        write_svg(item_dir / f"{stem}_pattern.svg", page.rect, layers, text_layers, only=pattern_layers)
        if previews:
            svg_to_png(item_dir / f"{stem}_pattern.svg", item_dir / f"{stem}_pattern.png")

    annotations = [
        {
            "text": s.text.strip(),
            "kind": classify_text(s.text),
            "inch": parse_inch(s.text),
            "layer": s.layer,
            "x_pt": round(s.cx, 1),
            "y_pt": round(s.cy, 1),
            "x_mm": round(s.cx * PT_TO_MM, 1),
            "y_mm": round(s.cy * PT_TO_MM, 1),
        }
        for s in spans
        if s.layer in pattern_layers
    ]
    counts = {k: len(v) for k, v in layers.items()}
    return {
        "page": pno,
        "size": size_table,
        "annotations": annotations,
        "notes": notes_markdown(pno, spans, regions, page.rect.width),
        "layer_counts": counts,
        "has_pattern": has_pattern,
        "last_band": regions.bands[-1][2],
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, nargs="*", help="이 페이지만 처리 (디버그)")
    ap.add_argument("--no-preview", action="store_true", help="PNG 미리보기 생략")
    args = ap.parse_args(argv)

    doc = pymupdf.open(str(PDF))
    items = build_index(doc)
    index = []
    for item in items:
        pages = [p for p in item.pages if not args.pages or p in args.pages]
        if not pages:
            continue
        item_dir = OUT / item.category / item.slug
        item_dir.mkdir(parents=True, exist_ok=True)
        size, annotations, notes, page_summ = None, {}, [], []
        last_band = "pattern"
        for pno in pages:
            r = process_page(doc, pno, item_dir, item, previews=not args.no_preview, prev_last_band=last_band)
            last_band = r["last_band"]
            if r["size"] and size is None:
                size = r["size"]
            annotations[f"p{pno:03d}"] = r["annotations"]
            if r["notes"]:
                notes.append(r["notes"])
            page_summ.append({"page": pno, "has_pattern": r["has_pattern"], "layers": r["layer_counts"]})
            print(f"  p{pno:03d} {item.slug:<32} {r['layer_counts']}", file=sys.stderr)
        if size:
            (item_dir / "size.json").write_text(json.dumps(size, ensure_ascii=False, indent=2), encoding="utf-8")
        (item_dir / "annotations.json").write_text(
            json.dumps(annotations, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        if notes:
            head = f"# {item.title_en}" + (f" ({item.title_ko})" if item.title_ko else "") + "\n"
            (item_dir / "notes.md").write_text(head + "".join(notes), encoding="utf-8")
        index.append({**asdict(item), "dir": str(item_dir.relative_to(ROOT)), "size": size, "page_summary": page_summ})

    if not args.pages:
        (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
        write_index_md(index)
    print(f"done: {len(index)} items → {OUT}", file=sys.stderr)


def write_index_md(index):
    out = ["# 포트폴리오 추출 목록\n", "원본: `reference/portfolio.pdf` (100p, 벡터). 단위는 인치.\n"]
    by_cat = defaultdict(list)
    for it in index:
        by_cat[(it["category"], it["category_ko"])].append(it)
    for (cat, cat_ko), its in by_cat.items():
        out.append(f"\n## {cat_ko} ({cat})\n")
        out.append("| # | 아이템 | 페이지 | 신체 사이즈 | 패턴 사이즈 | 폴더 |")
        out.append("|---|---|---|---|---|---|")
        for it in its:
            pages = f"{it['pages'][0]}–{it['pages'][-1]}" if len(it["pages"]) > 1 else str(it["pages"][0])
            body = pat = ""
            if it["size"]:
                body = ", ".join(f"{m['name']} {m['body']}" for m in it["size"]["measurements"] if m["body"])
                pat = ", ".join(f"{m['name']} {m['pattern']}" for m in it["size"]["measurements"] if m["pattern"])
            name = it["title_en"] + (f" ({it['title_ko']})" if it["title_ko"] else "")
            out.append(f"| {it['num'] or ''} | {name} | {pages} | {body} | {pat} | `{it['dir']}` |")
    (OUT / "index.md").write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
