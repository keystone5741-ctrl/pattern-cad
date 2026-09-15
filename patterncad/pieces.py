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
def _simplify(pts: list, tol: float = 0.004) -> list:
    """거의 일직선인 점을 뺀다 (Douglas-Peucker). 곡선을 촘촘히 찍으면 0.01" 짜리 물결이 생겨 시접 오프셋이 스스로 꼬인다."""
    if len(pts) < 3:
        return list(pts)
    a, b = pts[0], pts[-1]
    d = b - a
    L2 = d.dot(d)
    best, idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        p = pts[i]
        if L2 < 1e-12:
            dist = p.dist(a)
        else:
            t = max(0.0, min(1.0, (p - a).dot(d) / L2))
            dist = p.dist(a + d * t)
        if dist > best:
            best, idx = dist, i
    if best <= tol:
        return [a, b]
    return _simplify(pts[:idx + 1], tol)[:-1] + _simplify(pts[idx:], tol)


def _poly(l: ResolvedLine) -> list:
    pts = l.polyline(24)
    out = [pts[0]]
    for p in pts[1:]:
        if p.dist(out[-1]) > 1e-6:
            out.append(p)
    return _simplify(out) if l.kind == "curve" else out


def _cross(a: Pt, b: Pt, c: Pt, d: Pt):
    """선분 ab 와 cd 가 속에서 만나면 그 점."""
    def cr(o, p, q):
        return (p.x - o.x) * (q.y - o.y) - (p.y - o.y) * (q.x - o.x)
    if cr(a, b, c) * cr(a, b, d) < 0 and cr(c, d, a) * cr(c, d, b) < 0:
        try:
            return intersect_lines(a, b, c, d)
        except (ValueError, ZeroDivisionError):
            return None
    return None


def remove_small_loops(pts: list, max_len: float = 8.0) -> list:
    """재단선이 스스로 꼬인 작은 고리(오목한 곡선·모서리를 밖으로 밀 때 생긴다)를 교점으로 잘라 낸다.
    교차점 양쪽 호 가운데 짧은 쪽이 max_len 이하면 그쪽을 버린다. 큰 고리는 그대로."""
    pts = list(pts)

    def arc_len(seq):
        return sum(seq[k].dist(seq[k + 1]) for k in range(len(seq) - 1))
    for _ in range(50):
        n = len(pts)
        found = None
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            for j in range(i + 2, n):
                if i == 0 and j == n - 1:
                    continue
                c, d = pts[j], pts[(j + 1) % n]
                x = _cross(a, b, c, d)
                if x is None:
                    continue
                inner = pts[i + 1:j + 1]                       # 교점 → i+1 … j → 교점
                outer = pts[j + 1:] + pts[:i + 1]              # 교점 → j+1 … n-1, 0 … i → 교점
                li = arc_len(inner) + x.dist(inner[0]) + x.dist(inner[-1])
                lo = arc_len(outer) + x.dist(outer[0]) + x.dist(outer[-1])
                if min(li, lo) <= max_len:
                    found = (i, j, x, li <= lo)
                    break
            if found:
                break
        if not found:
            break
        i, j, x, drop_inner = found
        pts = (pts[:i + 1] + [x] + pts[j + 1:]) if drop_inner else (pts[i + 1:j + 1] + [x])
    return pts


def _split_at_junctions(polys: list) -> list:
    """T 자로 만나는 자리에서 꺾은선을 자른다 — 겹트임 덧단이 뒤중심선 가운데서 갈라지거나,
    고어 절개선이 허리선 가운데서 시작하는 경우."""
    polys = [(l, list(pts)) for l, pts in polys]
    changed = True
    rounds = 0
    while changed and rounds < 20:
        changed = False
        rounds += 1
        ends = [q for _, pts in polys for q in (pts[0], pts[-1])]
        for i, (l, pts) in enumerate(polys):
            for q in ends:
                if q.dist(pts[0]) <= TOL or q.dist(pts[-1]) <= TOL:
                    continue
                # 꺾은선 가운데 꼭짓점에 다른 선 끝이 닿는 경우 (고어 절개선의 퍼짐 시작점)
                kv = next((k for k in range(1, len(pts) - 1) if q.dist(pts[k]) <= TOL), None)
                if kv is not None:
                    polys[i] = (l, pts[:kv + 1])
                    polys.append((l, pts[kv:]))
                    changed = True
                    break
                for k in range(len(pts) - 1):
                    a, b = pts[k], pts[k + 1]
                    d = b - a
                    L2 = d.dot(d)
                    if L2 < 1e-12:
                        continue
                    t = (q - a).dot(d) / L2
                    if t <= 1e-6 or t >= 1 - 1e-6:
                        continue
                    f = a + d * t
                    if f.dist(q) <= TOL:
                        polys[i] = (l, pts[:k + 1] + [f])
                        polys.append((l, [f] + pts[k + 1:]))
                        changed = True
                        break
                if changed:
                    break
            if changed:
                break
    return polys


def _inside(p: Pt, loop: list) -> bool:
    c = False
    n = len(loop)
    for i in range(n):
        a, b = loop[i], loop[(i + 1) % n]
        if (a.y > p.y) != (b.y > p.y):
            x = a.x + (p.y - a.y) * (b.x - a.x) / (b.y - a.y)
            if p.x < x:
                c = not c
    return c


def _signed_angle(a: Pt, b: Pt) -> float:
    d = math.atan2(b.y, b.x) - math.atan2(a.y, a.x)
    while d <= -math.pi:
        d += 2 * math.pi
    while d > math.pi:
        d -= 2 * math.pi
    return d


def chain(lines: list, warnings: list) -> tuple:
    """외곽선·골선으로 평면 그래프를 만들고 **바깥 면**을 따라 닫힌 고리를 찾는다.
    → (고리 목록 [[Edge…]…], 안쪽에 남은 선 [(이름, 꺾은선)…], 고리별 경고)

    T 자로 만나는 자리는 잘라서 꼭짓점으로 두고, 끝이 하나뿐인 꼭짓점끼리는 가까우면(BRIDGE_MAX)
    곧게 이어 붙인다 (다트로 벌어진 허리선·옆선). 그다음 가장 왼쪽 꼭짓점에서 출발해 늘 가장 바깥쪽으로
    꺾으며 돌면 바깥 면이 나온다 — 겹트임 덧단은 바깥이고 뒤중심선 아래쪽은 안쪽 접는 선, 고어처럼
    떨어진 덩어리는 각각 고리가 된다. 고리에 쓰이지 않은 선은 안쪽 선으로 돌려준다."""
    polys = _split_at_junctions([(l, _poly(l)) for l in lines if len(_poly(l)) >= 2])
    if not polys:
        return [], [], []
    verts: list = []

    def vid(p):
        for i, v in enumerate(verts):
            if v.dist(p) <= TOL:
                return i
        verts.append(p)
        return len(verts) - 1
    edges = [{"a": vid(pts[0]), "b": vid(pts[-1]), "pts": pts, "name": l.name, "role": l.role, "syn": False}
             for l, pts in polys]
    deg: dict = {}
    for e in edges:
        deg[e["a"]] = deg.get(e["a"], 0) + 1
        deg[e["b"]] = deg.get(e["b"], 0) + 1
    ends = [v for v in range(len(verts)) if deg.get(v, 0) == 1]
    paired: set = set()
    for v in ends:
        if v in paired:
            continue
        best = None
        for w in ends:
            if w == v or w in paired:
                continue
            d = verts[v].dist(verts[w])
            if d <= BRIDGE_MAX and (best is None or d < best[0]):
                best = (d, w)
        if best:
            d, w = best
            na = next(e["name"] for e in edges if v in (e["a"], e["b"]))
            nb = next(e["name"] for e in edges if w in (e["a"], e["b"]))
            edges.append({"a": v, "b": w, "pts": [verts[v], verts[w]], "name": f"{na}→{nb}", "role": "bridge", "syn": True})
            paired.update((v, w))
            if d > 2.0:
                warnings.append(f"{na} 끝과 {nb} 끝이 {d:.2f}\" 떨어져 곧게 이었다")
    adj: dict = {}
    for i, e in enumerate(edges):
        adj.setdefault(e["a"], []).append((i, 1))
        adj.setdefault(e["b"], []).append((i, -1))
    seen: set = set()
    comps = []
    for v in adj:
        if v in seen:
            continue
        stack, comp = [v], set()
        while stack:
            x = stack.pop()
            if x in comp:
                continue
            comp.add(x)
            for i, sgn in adj[x]:
                stack.append(edges[i]["b"] if sgn == 1 else edges[i]["a"])
        seen |= comp
        comps.append(comp)
    used_edges: set = set()
    loops = []
    internals = []

    def trace(avail: set):
        """남은 선들만으로 가장 왼쪽 꼭짓점에서 출발해 늘 가장 바깥쪽으로 꺾어 돈다."""
        verts_in = {edges[i]["a"] for i in avail} | {edges[i]["b"] for i in avail}
        start = min(verts_in, key=lambda v: (verts[v].x, verts[v].y))
        v, incoming, seq, used = start, Pt(0, 1), [], set()
        while len(seq) < 500:
            cands = []
            for i, sgn in adj[v]:
                if i not in avail or (i, sgn) in used:
                    continue
                pts = edges[i]["pts"] if sgn == 1 else list(reversed(edges[i]["pts"]))
                out = (pts[1] - pts[0]).unit()
                back = seq and seq[-1][0] == i           # 방금 온 선을 되돌아가는 것은 마지막 수단
                cands.append((1 if back else 0, _signed_angle(incoming, out), i, sgn, pts))
            if not cands:
                break
            cands.sort(key=lambda c: (c[0], c[1]))       # 가장 바깥쪽(시계 방향으로 가장 크게 꺾는) 선
            _, _, i, sgn, pts = cands[0]
            used.add((i, sgn))
            seq.append((i, sgn, pts))
            incoming = (pts[-1] - pts[-2]).unit()
            v = edges[i]["b"] if sgn == 1 else edges[i]["a"]
            if v == start:
                break
        # 갔다가 되돌아온 선(막다른 선)은 고리에서 뺀다
        orig = list(seq)
        changed = True
        while changed and seq:
            changed = False
            for k in range(len(seq) - 1):
                if seq[k][0] == seq[k + 1][0] and seq[k][1] == -seq[k + 1][1]:
                    del seq[k:k + 2]
                    changed = True
                    break
            if not changed and len(seq) >= 2 and seq[0][0] == seq[-1][0] and seq[0][1] == -seq[-1][1]:
                seq = seq[1:-1]
                changed = True
        if len(seq) < 2 and len(orig) >= 2:      # 고리가 아니라 한 줄로 이어진 선들 — 간 길만 잡고 곧게 닫는다
            k = next((k for k in range(len(orig) - 1) if orig[k][0] == orig[k + 1][0] and orig[k][1] == -orig[k + 1][1]), len(orig) - 1)
            seq = orig[:k + 1]
        if len(seq) == 1 and edges[seq[0][0]]["a"] != edges[seq[0][0]]["b"]:
            return []
        return seq

    loop_warnings = []
    for comp in comps:
        avail = {i for i, e in enumerate(edges) if e["a"] in comp}
        while avail:
            seq = trace(avail)
            if not seq:
                break
            n_before = len(warnings)
            loop = []
            for i, sgn, pts in seq:
                e = edges[i]
                loop.append(Edge(e["name"], e["role"], pts, default_allowance(e["name"], e["role"]), e["syn"]))
                used_edges.add(i)
                avail.discard(i)
            if loop[-1].pts[-1].dist(loop[0].pts[0]) > TOL:
                gap = loop[-1].pts[-1].dist(loop[0].pts[0])
                loop.append(Edge("닫음", "bridge", [loop[-1].pts[-1], loop[0].pts[0]], loop[-1].allowance, True))
                if gap > 2.0:
                    warnings.append(f"외곽선이 {gap:.2f}\" 벌어져 곧게 닫았다")
            loops.append(loop)
            loop_warnings.append(warnings[n_before:])
            # 남은 선 중 이 고리 안에 든 것(겹트임 접는 선 같은 안쪽 선)은 안쪽 선. 밖에 남은 것은 다른 조각(고어)
            loop_pts = [p for e in loop for p in e.pts[:-1]]
            inside = {i for i in avail
                      if all(_inside(p, loop_pts) or any(p.dist(q) <= TOL for q in loop_pts) for p in edges[i]["pts"])}
            for i in inside:
                if not edges[i]["syn"]:
                    internals.append((edges[i]["name"], edges[i]["pts"]))
                used_edges.add(i)
            avail -= inside
    internals += [(e["name"], e["pts"]) for i, e in enumerate(edges) if i not in used_edges and not e["syn"]]
    if not loops and edges:
        warnings.append("외곽선이 닫히지 않는다")
    return loops, internals, loop_warnings


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
    groups: dict = {}   # 재단 조각 이름 → 선들. cut_piece 가 있으면 그것(여러 조각에 겹쳐 들어갈 수 있다), 없으면 piece
    for l in res.lines:
        if not getattr(l, "cut", True):
            continue
        for n in (getattr(l, "cut_piece", None) or [l.piece or ""]):
            groups.setdefault(n, []).append(l)
    out = []
    for pc, lines in groups.items():
        outline = [l for l in lines if l.role == "outline"]
        ends = [p for l in outline for p in (l.pts[0], l.pts[-1])]
        # 골선은 양 끝이 완성선 끝점에 닿을 때만 외곽이다 — 바지 주름선처럼 조각 가운데를 지나는 접는 선은 안쪽 선
        folds = [l for l in lines if l.role == "fold"
                 and all(any(q.dist(p) <= TOL for p in ends) for q in (l.pts[0], l.pts[-1]))]
        boundary = outline + folds
        if not boundary:
            continue
        warnings: list = []
        loops, inner, loop_warnings = chain(boundary, warnings)
        shared = [w for w in warnings if not any(w in lw for lw in loop_warnings)]   # 이어 붙이기 경고는 첫 조각에
        for li, loop_edges in enumerate(loops):
            name = (pc or res.block.name) + (f"{li + 1}" if len(loops) > 1 else "")
            ws = loop_warnings[li] + (shared if li == 0 else [])
            out.append(_make_piece(name, pc, block_key, res, lines, loop_edges, inner, ws,
                                   settings.get(f"{block_key}.{name}", settings.get(f"{block_key}.{pc}", {})), len(loops) > 1))
    return out


def _make_piece(name, pc, block_key, res, lines, edges, inner, warnings, st, multi) -> Piece:
        piece = Piece(name, block_key)
        piece.edges = edges
        piece.warnings = list(warnings)
        loop_pts = [p for e in edges for p in e.pts[:-1]]
        if multi:   # 고리가 여럿이면 안쪽 선은 그 고리 안에 든 것만
            lines = [l for l in lines if l.role in ("outline", "fold") or _inside(l.pts[0], loop_pts) or any(l.pts[0].dist(q) <= TOL for q in loop_pts)]
            inner = [(n, pts) for n, pts in inner if _inside(pts[len(pts) // 2], loop_pts)]
        fold_edge = next((e for e in piece.edges if e.role == "fold"), None)
        piece.fold = fold_edge.name if fold_edge else None
        for e in piece.edges:
            if e.name in (st.get("allowance") or {}):
                e.allowance = float(st["allowance"][e.name])
            elif "default_allowance" in st and e.role != "fold" and not e.synthetic:
                e.allowance = float(st["default_allowance"])
        piece.quantity = int(st.get("quantity", 1 if fold_edge else 2))
        piece.fabric = st.get("fabric", "겉감")
        folds = {e.name for e in edges if e.role == "fold"}
        piece.internal = [(l.name, l.role, _poly(l)) for l in lines
                          if l.role in ("dart", "mark", "notch", "grain", "construction") or (l.role == "fold" and l.name not in folds)]
        piece.internal += [(n, "fold", pts) for n, pts in inner]   # 고리 안에 남은 완성선 — 겹트임 접는 선 등
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
        piece.cut = remove_small_loops(piece.cut)
        piece.notches = notches_for(piece, lines)
        if piece.unfolded:      # 펼친 쪽에도 같은 노치
            a, b = fold_edge.pts[0], fold_edge.pts[-1]

            def M2(p):
                f = foot_of_perpendicular(p, a, b)
                return f + (f - p)
            piece.notches += [(M2(q), M2(q + n) - M2(q)) for q, n in list(piece.notches)
                              if q.dist(foot_of_perpendicular(q, a, b)) > 0.05]
        piece.grain = grain_for(piece, lines)
        return piece


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
