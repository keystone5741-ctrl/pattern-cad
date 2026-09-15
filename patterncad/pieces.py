"""조각(piece) — 재단 단위.

계산된 원형(Resolved)의 선을 조각별로 모아 **닫힌 외곽선**을 잇고, 변마다 **시접**을 붙여 재단선을 만들고,
다트 다리·노치 표시에서 **노치**를, 식서선이 없으면 세로로 **식서**를 정하고, 골선 조각은 펼칠 수 있다.

변(edge)에 이름이 있으니(옆솔기·밑단·목선…) 시접이 자동으로 따라온다:
    밑단·부리 1", 목선·암홀·소매산 3/8", 골선 0, 그 밖에 1/2"  — 프로젝트에서 변마다 고칠 수 있다.

외곽선이 끊긴 곳(옆솔기 가슴다트 자리처럼 다트 폭만큼 벌어진 곳)은 곧게 이어 붙이고 양 끝에 노치를 둔다.
다트를 접어 자르는 다트 캡은 아직 없다.
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


def offset_loop(edges: list) -> list:
    """변마다 시접을 붙인 재단선. 변과 변 사이는 마이터로 잇고, 안 되면 두 끝을 그대로 둔다."""
    loop = [p for e in edges for p in e.pts[:-1]]
    if not loop:
        return []
    out_sign = 1.0 if signed_area(loop) > 0 else -1.0
    offs = [_offset_polyline(e.pts, e.allowance, out_sign) for e in edges]
    cut = []
    n = len(offs)
    for i in range(n):
        cur, nxt = offs[i], offs[(i + 1) % n]
        seg = cur[1:-1] if i > 0 else cur[:-1]     # 첫 변은 앞 변과의 모서리에서 정리된다
        if i == 0:
            seg = cur[:-1]
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
    # 첫 변의 시작점은 마지막 모서리로 대체된다
    if cut and len(edges) > 1:
        cut = cut[1:]
    return cut


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
        boundary = [l for l in lines if l.role in ("outline", "fold")]
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
                          if l.role in ("dart", "mark", "notch", "grain", "construction")]
        if fold_edge and st.get("unfold"):
            piece.edges = unfold(piece.edges, fold_edge.name)
            piece.unfolded = True
            a, b = fold_edge.pts[0], fold_edge.pts[-1]

            def M(p):
                f = foot_of_perpendicular(p, a, b)
                return f + (f - p)
            piece.internal += [(n + "'", r, [M(p) for p in pts]) for n, r, pts in piece.internal]
            piece.internal.append((fold_edge.name, "fold", list(fold_edge.pts)))
        piece.loop = [p for e in piece.edges for p in e.pts[:-1]]
        piece.cut = offset_loop(piece.edges)
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
