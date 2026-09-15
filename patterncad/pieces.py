"""조각(piece) — 재단 단위.

계산된 원형(Resolved)의 선을 조각별로 모아 **닫힌 외곽선**을 잇고, 변마다 **시접**을 붙여 재단선을 만들고,
다트 다리·노치 표시에서 **노치**를, 식서선이 없으면 세로로 **식서**를 정하고, 골선 조각은 펼칠 수 있다.

변(edge)에 이름이 있으니(옆솔기·밑단·목선…) 시접이 자동으로 따라온다:
    밑단·부리 1", 목선·암홀·소매산 3/8", 골선 0, 그 밖에 1/2"  — 프로젝트에서 변마다 고칠 수 있다.

외곽선이 끊긴 곳(옆솔기 가슴다트 자리처럼 다트 폭만큼 벌어진 곳)은 곧게 이어 붙이고 양 끝에 노치를 두며,
재단선에는 다트를 접어 자른 모양의 **다트 캡**을 붙인다 (접는 쪽 기본 아래, 조각 설정 dart_fold: up 으로 바꿀 수 있다).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .block import Resolved, ResolvedLine
from .geometry import Pt, foot_of_perpendicular, intersect_lines

TOL = 0.02          # 점이 같다고 볼 거리 (인치)
BRIDGE_MAX = 3.0    # 이 안에 있는 끊긴 끝은 곧게 이어 준다
NOTCH_LEN = 0.25

DEFAULT_ALLOWANCE = {"hem": 1.0, "neck": 0.375, "fold": 0.0, "other": 0.5}


def default_allowance(name: str, role: str) -> float:
    if role == "fold":
        return DEFAULT_ALLOWANCE["fold"]
    if any(k in name for k in ("밑단", "부리")):     # 칼라 밑변·오비 밑선은 봉제선이라 1/2
        return DEFAULT_ALLOWANCE["hem"]
    if any(k in name for k in ("목", "네크", "암홀", "소매산")):
        return DEFAULT_ALLOWANCE["neck"]
    return DEFAULT_ALLOWANCE["other"]


@dataclass
class Edge:
    name: str
    role: str
    pts: list           # 외곽을 도는 방향으로 정렬된 꺾은선
    allowance: float = 0.5
    synthetic: bool = False


@dataclass
class Piece:
    name: str
    block: str
    edges: list = field(default_factory=list)
    loop: list = field(default_factory=list)      # 완성선 (닫힘)
    cut: list = field(default_factory=list)       # 재단선 (닫힘)
    internal: list = field(default_factory=list)  # 다트·표시 등 안쪽 선 [(이름, 역할, 꺾은선)]
    notches: list = field(default_factory=list)   # [(완성선 위 점, 바깥 방향)]
    grain: tuple | None = None
    fold: str | None = None                       # 골선 변 이름 (펼치지 않았을 때)
    unfolded: bool = False
    quantity: int = 1
    fabric: str = "겉감"
    warnings: list = field(default_factory=list)

    def bbox(self):
        pts = self.cut or self.loop
        return (min(p.x for p in pts), min(p.y for p in pts), max(p.x for p in pts), max(p.y for p in pts))


# ------------------------------------------------------------------ 외곽선 잇기
def _poly(l: ResolvedLine) -> list:
    pts = l.polyline(24)
    out = [pts[0]]
    for p in pts[1:]:
        if p.dist(out[-1]) > 1e-6:
            out.append(p)
    return out


def chain(lines: list, warnings: list) -> list:
    """외곽선·골선을 끝점끼리 이어 닫힌 고리로. 끊긴 곳은 곧게 이어 붙인다 (synthetic)."""
    pool = [(l, _poly(l)) for l in lines if len(_poly(l)) >= 2]
    if not pool:
        return []
    l0, p0 = pool.pop(0)
    edges = [Edge(l0.name, l0.role, p0, default_allowance(l0.name, l0.role))]
    while True:
        end = edges[-1].pts[-1]
        if len(edges) > 1 and end.dist(edges[0].pts[0]) <= TOL:
            break
        best = None
        for i, (l, pts) in enumerate(pool):
            for rev in (False, True):
                q = pts[-1] if rev else pts[0]
                d = end.dist(q)
                if best is None or d < best[0]:
                    best = (d, i, rev)
        if best is None:
            # 남은 선이 없다 — 시작점으로 곧게 닫는다
            if end.dist(edges[0].pts[0]) > TOL:
                edges.append(Edge("닫음", "bridge", [end, edges[0].pts[0]], edges[-1].allowance, True))
                if end.dist(edges[0].pts[0]) > 2.0:
                    warnings.append(f"외곽선이 {end.dist(edges[0].pts[0]):.2f}\" 벌어져 곧게 닫았다")
            break
        d, i, rev = best
        l, pts = pool.pop(i)
        if rev:
            pts = list(reversed(pts))
        if d > TOL:
            if d > BRIDGE_MAX:
                warnings.append(f"{edges[-1].name} 끝과 {l.name} 시작이 {d:.2f}\" 떨어져 있다")
            edges.append(Edge(f"{edges[-1].name}→{l.name}", "bridge", [end, pts[0]], edges[-1].allowance, True))
        edges.append(Edge(l.name, l.role, pts, default_allowance(l.name, l.role)))
    if pool:
        warnings.append("외곽선에 끼지 못한 선: " + ", ".join(l.name for l, _ in pool))
    return edges


def signed_area(pts: list) -> float:
    a = 0.0
    for i in range(len(pts)):
        p, q = pts[i], pts[(i + 1) % len(pts)]
        a += p.x * q.y - q.x * p.y
    return a / 2


# ------------------------------------------------------------------ 시접
def _offset_polyline(pts: list, a: float, out_sign: float) -> list:
    """꺾은선을 바깥으로 a 만큼 평행 이동 (꼭짓점은 마이터, 너무 뾰족하면 2배까지)."""
    if a == 0 or len(pts) < 2:
        return list(pts)
    segs = []
    for i in range(len(pts) - 1):
        d = pts[i + 1] - pts[i]
        if d.length() < 1e-9:
            continue
        n = Pt(d.y, -d.x).unit() * out_sign
        segs.append((pts[i] + n * a, pts[i + 1] + n * a))
    if not segs:
        return list(pts)
    out = [segs[0][0]]
    for (a0, a1), (b0, b1) in zip(segs, segs[1:]):
        try:
            x = intersect_lines(a0, a1, b0, b1)
        except (ValueError, ZeroDivisionError):
            x = None
        if x is None or x.dist(a1) > 2.0 * a + 0.01 or math.isnan(x.x):
            out.append(a1)
            out.append(b0)
        else:
            out.append(x)
    out.append(segs[-1][1])
    return out


def offset_loop(edges: list, return_corners: bool = False):
    """변마다 시접을 붙인 재단선. 변과 변 사이는 마이터로 잇고, 안 되면 두 끝을 그대로 둔다.
    return_corners 면 (재단선, 변 i 끝 모서리의 재단선 인덱스 목록, 변별 오프셋 꺾은선) 도 함께."""
    loop = [p for e in edges for p in e.pts[:-1]]
    if not loop:
        return ([], [], []) if return_corners else []
    out_sign = 1.0 if signed_area(loop) > 0 else -1.0
    offs = [_offset_polyline(e.pts, e.allowance, out_sign) for e in edges]
    cut = []
    corner_at = []
    n = len(offs)
    for i in range(n):
        cur, nxt = offs[i], offs[(i + 1) % n]
        # 모서리: 이 변의 마지막 조각과 다음 변의 첫 조각을 연장해 만나는 점
        a0, a1 = cur[-2], cur[-1]
        b0, b1 = nxt[0], nxt[1]
        try:
            x = intersect_lines(a0, a1, b0, b1)
        except (ValueError, ZeroDivisionError):
            x = None
        big = 3.0 * max(edges[i].allowance, edges[(i + 1) % n].allowance, 0.25)
        if x is None or math.isnan(x.x) or x.dist(a1) > big:
            corner = [a1, b0] if a1.dist(b0) > 1e-6 else [a1]
        else:
            corner = [x]
        if i == 0:
            cut.extend(cur[:-1])
        else:
            cut.extend(cur[1:-1])
        cut.extend(corner)
        corner_at.append(len(cut) - 1)            # 변 i 의 끝 모서리 (다음 변의 시작 모서리)
    # 첫 변의 시작점은 마지막 모서리로 대체된다
    if cut and len(edges) > 1:
        cut = cut[1:]
        corner_at = [c - 1 for c in corner_at]
    return (cut, corner_at, offs) if return_corners else cut


def dart_caps(edges: list, cut: list, corner_at: list, offs: list, darts: list, fold: str = "down") -> list:
    """다트 자리(곧게 이어 붙인 변)에 **다트 캡**을 붙인다.

    다트를 접어 자르고 펼치면 시접이 삼각형으로 튀어나온다. 접는 쪽(기본: 아래 = y 가 큰 쪽) 이웃 변의
    재단선을 다트 중심선(꼭짓점 ↔ 벌어진 자리 가운데)까지 연장한 점이 캡의 꼭짓점이고,
    반대쪽 재단선은 그 점의 거울상이라 결국 A' → 꼭짓점 → B' 가 된다."""
    n = len(edges)
    out = list(cut)
    inserts = []
    for i, e in enumerate(edges):
        if not e.synthetic or len(e.pts) != 2:
            continue
        a, b = e.pts[0], e.pts[-1]
        apex = None
        for d in darts:
            if (d[0].dist(a) < 0.05 and d[-1].dist(b) < 0.05) or (d[0].dist(b) < 0.05 and d[-1].dist(a) < 0.05):
                apex = d[len(d) // 2]
                break
        if apex is None:
            continue
        mid = (a + b) * 0.5
        if mid.dist(apex) < 1e-6:
            continue
        # 접는 쪽 이웃 변: down 이면 y 가 큰 끝의 이웃
        prev_i, next_i = (i - 1) % n, (i + 1) % n
        a_low = (a.y > b.y) if fold == "down" else (a.y < b.y)
        nb = prev_i if a_low else next_i
        seg = (offs[nb][-2], offs[nb][-1]) if nb == prev_i else (offs[nb][0], offs[nb][1])
        try:
            x = intersect_lines(seg[0], seg[1], apex, mid)
        except (ValueError, ZeroDivisionError):
            continue
        if math.isnan(x.x) or (x - mid).dot(mid - apex) <= 0 or x.dist(mid) > 4 * max(e.allowance, 0.25):
            continue
        # 재단선에서 이 변의 시작 모서리와 끝 모서리 사이에 꼭짓점을 끼운다
        end_idx = corner_at[i]
        inserts.append((end_idx, x))
    for end_idx, x in sorted(inserts, reverse=True):
        out.insert(end_idx, x)
    return out


# ------------------------------------------------------------------ 노치 · 식서 · 펼치기
def _on_loop(p: Pt, loop: list) -> tuple | None:
    """점이 완성선 위에 있으면 (그 점, 바깥 방향)."""
    best = None
    for i in range(len(loop)):
        a, b = loop[i], loop[(i + 1) % len(loop)]
        d = b - a
        L = d.length()
        if L < 1e-9:
            continue
        t = max(0.0, min(1.0, (p - a).dot(d) / (L * L)))
        q = a + d * t
        dist = q.dist(p)
        if best is None or dist < best[0]:
            best = (dist, q, d)
    if best and best[0] <= 0.05:
        return best[1], best[2]
    return None


def notches_for(piece: Piece, lines: list) -> list:
    out_sign = 1.0 if signed_area(piece.loop) > 0 else -1.0
    found = []

    def add(p):
        hit = _on_loop(p, piece.loop)
        if not hit:
            return
        q, d = hit
        n = Pt(d.y, -d.x).unit() * out_sign
        if all(q.dist(f[0]) > 0.1 for f in found):
            found.append((q, n))

    for l in lines:
        if l.role == "notch":
            add(l.pts[0])
        elif l.role == "dart":
            add(l.pts[0])
            add(l.pts[-1])
    for e in piece.edges:
        if e.synthetic:            # 끊겨서 이어 붙인 자리 양 끝 (다트 벌어진 자리)
            add(e.pts[0])
            add(e.pts[-1])
    return found


def grain_for(piece: Piece, lines: list) -> tuple:
    g = next((l for l in lines if l.role == "grain"), None)
    if g:
        return (g.pts[0], g.pts[-1])
    x0, y0, x1, y1 = piece.bbox()
    cx = (x0 + x1) / 2
    return (Pt(cx, y0 + (y1 - y0) * 0.2), Pt(cx, y1 - (y1 - y0) * 0.2))


def unfold(edges: list, fold_name: str) -> list:
    """골선 변을 축으로 나머지를 대칭해 한 장으로 펼친다."""
    i = next((k for k, e in enumerate(edges) if e.name == fold_name), None)
    if i is None:
        return edges
    fold = edges[i]
    a, b = fold.pts[0], fold.pts[-1]

    def M(p):
        f = foot_of_perpendicular(p, a, b)
        return f + (f - p)

    rest = edges[i + 1:] + edges[:i]          # 골선 끝에서 시작해 골선 시작으로 돌아오는 변들
    mirrored = [Edge(e.name + "'", e.role, [M(p) for p in reversed(e.pts)], e.allowance, e.synthetic)
                for e in reversed(rest)]
    return rest + mirrored


# ------------------------------------------------------------------ 전체
def build_pieces(res: Resolved, block_key: str, settings: dict | None = None) -> list:
    """조각 목록. settings 는 {"<block>.<piece>": {"quantity", "unfold", "fabric", "allowance": {변: 값}}}"""
    settings = settings or {}
    names = []
    for l in res.lines:
        pc = l.piece or ""
        if pc not in names:
            names.append(pc)
    out = []
    for pc in names:
        lines = [l for l in res.lines if (l.piece or "") == pc]
        outline = [l for l in lines if l.role == "outline"]
        ends = [p for l in outline for p in (l.pts[0], l.pts[-1])]
        # 골선은 양 끝이 완성선 끝점에 닿을 때만 외곽이다 — 바지 주름선처럼 조각 가운데를 지나는 접는 선은 안쪽 선
        folds = [l for l in lines if l.role == "fold"
                 and all(any(q.dist(p) <= TOL for p in ends) for q in (l.pts[0], l.pts[-1]))]
        boundary = outline + folds
        if not boundary:
            continue
        st = settings.get(f"{block_key}.{pc}", {})
        piece = Piece(pc or res.block.name, block_key)
        piece.edges = chain(boundary, piece.warnings)
        if not piece.edges:
            continue
        fold_edge = next((e for e in piece.edges if e.role == "fold"), None)
        piece.fold = fold_edge.name if fold_edge else None
        for e in piece.edges:
            if e.name in (st.get("allowance") or {}):
                e.allowance = float(st["allowance"][e.name])
            elif "default_allowance" in st and e.role != "fold" and not e.synthetic:
                e.allowance = float(st["default_allowance"])
        piece.quantity = int(st.get("quantity", 1 if fold_edge else 2))
        piece.fabric = st.get("fabric", "겉감")
        piece.internal = [(l.name, l.role, _poly(l)) for l in lines
                          if l.role in ("dart", "mark", "notch", "grain", "construction") or (l.role == "fold" and l not in folds)]
        M = None
        if fold_edge and st.get("unfold"):
            piece.edges = unfold(piece.edges, fold_edge.name)
            piece.unfolded = True
            a, b = fold_edge.pts[0], fold_edge.pts[-1]

            def M(p, a=a, b=b):
                f = foot_of_perpendicular(p, a, b)
                return f + (f - p)
            piece.internal += [(n + "'", r, [M(p) for p in pts]) for n, r, pts in piece.internal]
            piece.internal.append((fold_edge.name, "fold", list(fold_edge.pts)))
        piece.loop = [p for e in piece.edges for p in e.pts[:-1]]
        cut, corner_at, offs = offset_loop(piece.edges, return_corners=True)
        darts = [_poly(l) for l in lines if l.role == "dart"]
        if piece.unfolded:
            darts += [[M(p) for p in d] for d in darts]
        piece.cut = dart_caps(piece.edges, cut, corner_at, offs, darts, st.get("dart_fold", "down")) if st.get("dart_cap", True) else cut
        piece.notches = notches_for(piece, lines)
        if piece.unfolded:      # 펼친 쪽에도 같은 노치
            a, b = fold_edge.pts[0], fold_edge.pts[-1]

            def M2(p):
                f = foot_of_perpendicular(p, a, b)
                return f + (f - p)
            piece.notches += [(M2(q), M2(q + n) - M2(q)) for q, n in list(piece.notches)
                              if q.dist(foot_of_perpendicular(q, a, b)) > 0.05]
        piece.grain = grain_for(piece, lines)
        out.append(piece)
    return out


# ------------------------------------------------------------------ 마카용 변환
def transform_piece(pc: Piece, rot: float = 0.0, flip: bool = False, dx: float = 0.0, dy: float = 0.0) -> Piece:
    """조각을 뒤집고(x 대칭) 돌리고(도, 원점 기준) 옮긴 사본. 마카에 놓인 자리를 실제 좌표로 만들 때 쓴다."""
    def T(p: Pt) -> Pt:
        q = Pt(-p.x, p.y) if flip else p
        q = q.rotate(rot) if rot else q
        return Pt(q.x + dx, q.y + dy)

    def TN(n: Pt) -> Pt:      # 방향 벡터: 옮기지 않는다
        q = Pt(-n.x, n.y) if flip else n
        return q.rotate(rot) if rot else q
    out = Piece(pc.name, pc.block)
    out.edges = [Edge(e.name, e.role, [T(p) for p in e.pts], e.allowance, e.synthetic) for e in pc.edges]
    out.loop = [T(p) for p in pc.loop]
    out.cut = [T(p) for p in pc.cut]
    out.internal = [(n, r, [T(p) for p in pts]) for n, r, pts in pc.internal]
    out.notches = [(T(q), TN(n)) for q, n in pc.notches]
    out.grain = (T(pc.grain[0]), T(pc.grain[1])) if pc.grain else None
    out.fold, out.unfolded, out.quantity, out.fabric, out.warnings = pc.fold, pc.unfolded, pc.quantity, pc.fabric, list(pc.warnings)
    return out
