"""규칙으로 계산한 원형을 원본 도면 위에 **자동으로 맞춰 놓고** 모양 차이를 본다.

도면은 페이지마다 배율·위치가 다르다. 그래서 배율(가로·세로)과 원점을 직접 주는 대신
겹침 오차가 가장 작아지는 값을 찾아서 겹친다.

    python tools/align_block.py blocks/jacket_body.yaml --page 74
    python tools/align_block.py blocks/jacket_body.yaml --page 74 --piece 앞판
    python tools/align_block.py blocks/jacket_body.yaml --page 74 --fit   # 곡선 핸들 맞춤 YAML

출력: verify_<id>.svg / .png (원본 검정, 규칙 빨강) 과 선별 편차(인치).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import extract_portfolio as ex  # noqa: E402
from patterncad.block import Block  # noqa: E402
from patterncad.geometry import Pt, polyline_length  # noqa: E402
from patterncad.svg import render_group  # noqa: E402
from verify_block import fit_curve_handles  # noqa: E402


def page_polylines(page, dashed_ok=True, min_len_pt=6.0, layers=("pattern", "developed")):
    """도면 선을 pt 폴리라인 목록으로.

    절개-벌림으로 만드는 아이템(A라인·개더·플레어·트럼펫·플리츠 스커트)은 **전개한 뒤의 것이
    완성 패턴**이고, 그 그림은 '전개(developed)' 층에 있다. 그래서 기본으로 두 층을 함께 본다.
    """
    spans = ex.page_spans(page)
    lines = ex.page_lines(page)
    reg = ex.detect_regions(page, spans, lines, True)
    out = []
    for d in page.get_drawings():
        if ex.classify_drawing(d, reg) not in layers:
            continue
        if not dashed_ok and bool(d.get("dashes")) and d.get("dashes") != "[] 0":
            continue
        cur: list[Pt] = []
        for it in d["items"]:
            if it[0] == "l":
                a, b = Pt(it[1].x, it[1].y), Pt(it[2].x, it[2].y)
                if cur and cur[-1].dist(a) < 1e-3:
                    cur.append(b)
                else:
                    if len(cur) > 1:
                        out.append(cur)
                    cur = [a, b]
            elif it[0] == "c":
                from patterncad.geometry import Bezier

                bz = Bezier(*[Pt(q.x, q.y) for q in it[1:5]])
                pts = bz.sample(12)
                if cur and cur[-1].dist(pts[0]) < 1e-3:
                    cur.extend(pts[1:])
                else:
                    if len(cur) > 1:
                        out.append(cur)
                    cur = pts
        if len(cur) > 1:
            out.append(cur)
    return [pl for pl in out if polyline_length(pl) >= min_len_pt]


class Cloud:
    """폴리라인 다발을 점구름 + 격자 해시로 — 최근접 거리를 빨리 구하려고."""

    def __init__(self, polys, step=1.5):
        self.step = step
        self.pts = []
        for pl in polys:
            for a, b in zip(pl, pl[1:]):
                d = a.dist(b)
                n = max(1, int(d / step))
                for i in range(n + 1):
                    t = i / n
                    self.pts.append(Pt(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t))
        self.cell = 8.0
        self.grid: dict[tuple[int, int], list[Pt]] = {}
        for p in self.pts:
            self.grid.setdefault((int(p.x // self.cell), int(p.y // self.cell)), []).append(p)

    def nearest(self, p: Pt, cap: float) -> float:
        cx, cy = int(p.x // self.cell), int(p.y // self.cell)
        r = 0
        best = cap
        while r <= int(cap // self.cell) + 1:
            found = False
            for i in range(cx - r, cx + r + 1):
                for j in range(cy - r, cy + r + 1):
                    if r and max(abs(i - cx), abs(j - cy)) != r:
                        continue
                    for q in self.grid.get((i, j), ()):
                        d = p.dist(q)
                        if d < best:
                            best = d
                            found = True
            if found and r >= 1:
                break
            r += 1
        return best


RULER_BODY = ["진동깊이", "등길이", "엉덩이길이", "밑위길이", "무릎길이", "기장"]
FROM_WAIST = {"엉덩이길이", "밑위길이", "무릎길이"}   # 이것들은 허리선에서 잰다
RULER_PANTS = ["엉덩이길이", "밑위길이", "무릎길이", "기장"]
RULER_SLEEVE = ["소매산높이", "팔꿈치길이", "소매길이"]


def ruler_fit(page_no: int, kind: str = "body"):
    """도면 옆 눈금(진동깊이·등길이·기장 …)의 글자 위치로 세로 배율과 원점을 구한다.

    눈금 이름표는 자기 구간의 **가운데**에 놓인다. 구간의 누적 위치를 알고 있으므로
    (구간 중앙 인치, 글자 y) 짝을 최소제곱으로 맞추면 y = oy + inch * s 를 얻는다.
    도면은 정배율이므로 이 s 를 가로에도 그대로 쓴다.
    """
    import json as _json

    idx = _json.load(open(ROOT / "extracted" / "index.json", encoding="utf-8"))
    item = next((i for i in idx if page_no in i["pages"]), None)
    if not item:
        return None
    ann = _json.load(open(ROOT / item["dir"] / "annotations.json", encoding="utf-8")).get(f"p{page_no:03d}", [])
    names = {"body": RULER_BODY, "pants": RULER_PANTS, "sleeve": RULER_SLEEVE}[kind]
    # 이름표 바로 아래(12pt 이내, x 가 비슷한) 숫자를 값으로 짝짓는다
    found = []
    for a1 in ann:
        if a1["text"].strip() not in names:
            continue
        for a2 in ann:
            if a2 is a1 or not (0 < a2["y_pt"] - a1["y_pt"] < 14):
                continue
            if abs(a2["x_pt"] - a1["x_pt"]) > 24:
                continue
            v = ex.parse_inch(a2["text"])
            if v:
                found.append((a1["text"].strip(), v, (a1["y_pt"] + a2["y_pt"]) / 2, a1["x_pt"]))
                break
    if len(found) < 2:
        return None
    # 같은 눈금(x 가 가까운 것)끼리 묶는다. 한 장에 몸판 눈금과 소매 눈금이 따로 있을 수 있다
    found.sort(key=lambda f: f[3])
    groups: list[list] = []
    for f in found:
        if groups and f[3] - groups[-1][-1][3] < 24:
            groups[-1].append(f)
        else:
            groups.append([f])
    anchor = {"body": ("등길이", "진동깊이"), "pants": ("밑위길이", "기장"),
              "sleeve": ("소매산높이", "소매길이")}[kind]
    named = [g for g in groups if any(x[0] in anchor for x in g)]
    # 이름표가 둘뿐인 눈금은 최소제곱이 항상 딱 맞아 버려서 틀려도 걸러지지 않는다.
    # 그래서 짝이 셋 이상인 그룹이 있으면 그쪽을 먼저 쓴다.
    big = [g for g in named if len(g) >= 3] or [g for g in groups if len(g) >= 3]
    grp = max(big or named or groups, key=len)
    grp.sort(key=lambda f: f[2])
    pairs, cum, waist = [], 0.0, 0.0
    for name, v, y, _ in grp:
        new = waist + v if (kind == "body" and name in FROM_WAIST) else v
        if kind == "body" and name == "등길이":
            waist = v
        if new <= cum:
            continue
        pairs.append(((cum + new) / 2, y))
        cum = new
    if len(pairs) < 2:
        return None
    n = len(pairs)
    sx_ = sum(p[0] for p in pairs) / n
    sy_ = sum(p[1] for p in pairs) / n
    num = sum((p[0] - sx_) * (p[1] - sy_) for p in pairs)
    den = sum((p[0] - sx_) ** 2 for p in pairs)
    if den < 1e-9:
        return None
    s = num / den
    oy = sy_ - s * sx_
    resid = max(abs(p[1] - (oy + s * p[0])) for p in pairs)
    if not (5 < s < 45) or resid > 1.2 * s:
        return None
    return s, oy


def cluster_polylines(polys, gap=26.0):
    """도면 한 장에 몸판·소매·칼라가 따로 그려져 있으므로, 가까운 선끼리 묶어 그림 단위로 나눈다."""
    boxes = []
    for pl in polys:
        boxes.append((min(p.x for p in pl), min(p.y for p in pl),
                      max(p.x for p in pl), max(p.y for p in pl)))
    parent = list(range(len(polys)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def near(a, b):
        return (a[0] - gap <= b[2] and b[0] - gap <= a[2]
                and a[1] - gap <= b[3] and b[1] - gap <= a[3])

    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            if near(boxes[i], boxes[j]):
                pi, pj = find(i), find(j)
                if pi != pj:
                    parent[pi] = pj
    groups: dict[int, list] = {}
    for i, pl in enumerate(polys):
        groups.setdefault(find(i), []).append(pl)
    out = sorted(groups.values(), key=lambda g: -sum(polyline_length(pl) for pl in g))
    return out


def rotate_resolved(res, deg, center=None):
    """계산된 원형을 통째로 회전시킨 사본. 전개 조각은 도면에서 돌려 놓여 있어서 필요하다."""
    from patterncad.block import Resolved, ResolvedLine
    from patterncad.geometry import Bezier

    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    ct = center or Pt(0, 0)

    def R(p):
        x, y = p.x - ct.x, p.y - ct.y
        return Pt(x * c - y * s + ct.x, x * s + y * c + ct.y)
    lines = [ResolvedLine(l.name, l.role, l.kind, l.point_names, [R(p) for p in l.pts],
                          [Bezier(R(b.p0), R(b.c1), R(b.c2), R(b.p3)) for b in l.beziers],
                          l.piece, l.ko)
             for l in res.lines]
    return Resolved(res.block, res.measurements, {k: R(v) for k, v in res.points.items()},
                    res.point_meta, lines)


def block_points(res, n=10, cap=300):
    pts = []
    for l in res.lines:
        if l.role in ("outline", "dart"):
            pts.extend(l.polyline(n))
    if len(pts) > cap:                     # 맞춤 속도를 위해 솎아 낸다
        step = len(pts) / cap
        pts = [pts[int(i * step)] for i in range(cap)]
    return pts


def cost(pts, cloud, sx, sy, ox, oy, rot=0.0, cap=30.0):
    """원형 점들을 (회전 → 배율 → 이동) 시켜 도면 선까지의 평균 거리.

    전개(절개-벌림)한 조각은 도면에서 돌려 놓는 경우가 많아 회전도 맞춰야 한다."""
    s = 0.0
    if rot:
        c, sn = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        for p in pts:
            x, y = p.x * c - p.y * sn, p.x * sn + p.y * c
            s += cloud.nearest(Pt(x * sx + ox, y * sy + oy), cap)
    else:
        for p in pts:
            s += cloud.nearest(Pt(p.x * sx + ox, p.y * sy + oy), cap)
    return s / len(pts)


def xform(p: Pt, sx, sy, ox, oy, rot=0.0) -> Pt:
    if rot:
        c, sn = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        p = Pt(p.x * c - p.y * sn, p.x * sn + p.y * c)
    return Pt(p.x * sx + ox, p.y * sy + oy)


def optimise(pts, cloud, start, steps=(8.0, 3.0, 1.0, 0.35, 0.12, 0.04), uniform=False,
             smin=6.0, smax=30.0, rotate=False):
    """(sx, sy, ox, oy, 회전) 을 패턴 서치로 줄인다. sx·sy 는 pt/inch, ox·oy 는 pt, 회전은 도."""
    cur = list(start) + ([0.0] if len(start) == 4 else [])
    if uniform:
        cur[1] = cur[0]
    best = cost(pts, cloud, *cur)
    for st in steps:
        improved = True
        while improved:
            improved = False
            moves = ((0, st * 0.06), (2, st), (3, st)) if uniform else \
                    ((0, st * 0.06), (1, st * 0.06), (2, st), (3, st))
            if rotate:
                moves = moves + ((4, st * 0.9),)
            for k, scale in moves:
                for sgn in (1, -1):
                    trial = list(cur)
                    trial[k] += sgn * scale
                    if uniform:
                        trial[1] = trial[0]
                    if not (smin <= trial[0] <= smax and smin <= trial[1] <= smax):
                        continue
                    c = cost(pts, cloud, *trial)
                    if c < best - 1e-6:
                        best, cur, improved = c, trial, True
    return cur, best


def cluster_align(pts, cloud, polys, groups=4, aniso=False, allow_rot=False, min_ox=None, max_ox=None):
    """도면을 그림 단위로 나눠 각각 맞춰 보고 가장 잘 맞는 자리를 고른다.

    눈금이 없는 장(원형 페이지)에서 쓰고, 눈금으로 맞춘 결과가 시원찮을 때도 다시 써 본다.
    """
    bx0, bx1 = min(p.x for p in pts), max(p.x for p in pts)
    by0, by1 = min(p.y for p in pts), max(p.y for p in pts)
    best = None
    for grp in cluster_polylines(polys)[:groups]:
        px0 = min(p.x for pl in grp for p in pl)
        px1 = max(p.x for pl in grp for p in pl)
        py0 = min(p.y for pl in grp for p in pl)
        py1 = max(p.y for pl in grp for p in pl)
        if px1 - px0 < 20 or py1 - py0 < 20:
            continue
        starts = []
        for fx in (1.0, 0.85, 0.7, 0.55):
            for fy in (1.0, 0.85, 0.7, 0.55):
                sxx = (px1 - px0) / (bx1 - bx0) * fx
                syy = (py1 - py0) / (by1 - by0) * fy
                if not aniso:
                    sxx = syy = (sxx + syy) / 2
                for ax in (0.0, 0.5, 1.0):
                    for ay in (0.0, 0.5, 1.0):
                        oxx = px0 + (px1 - px0) * ax - (bx0 + (bx1 - bx0) * ax) * sxx
                        oyy = py0 + (py1 - py0) * ay - (by0 + (by1 - by0) * ay) * syy
                        if min_ox is not None and oxx < min_ox:
                            continue
                        if max_ox is not None and oxx > max_ox:
                            continue
                        if 6.0 <= sxx <= 30.0 and 6.0 <= syy <= 30.0:
                            starts.append([sxx, syy, oxx, oyy])
        if not starts:
            continue
        starts.sort(key=lambda s: cost(pts, cloud, *s))
        grp_area = (px1 - px0) * (py1 - py0)
        for s in starts[:3]:
            if allow_rot:
                s0, best_r = list(s) + [0.0], None
                for r in range(-60, 61, 6):     # 거친 각도 훑기
                    s0[4] = r
                    cr = cost(pts, cloud, *s0)
                    if best_r is None or cr < best_r[0]:
                        best_r = (cr, r)
                s = list(s) + [best_r[1]]
            got, c = optimise(pts, cloud, s, uniform=(not aniso), rotate=allow_rot)
            if min_ox is not None and got[2] < min_ox - 1:
                continue
            if max_ox is not None and got[2] > max_ox + 1:
                continue
            # 원형을 빽빽한 곳에 쪼그려 넣으면 오차가 작게 나온다.
            # 놓인 크기가 그림 크기와 비슷할 때만 인정한다
            area = (bx1 - bx0) * got[0] * (by1 - by0) * got[1]
            if not (0.4 <= area / grp_area <= 2.5):
                continue
            if best is None or c < best[1]:
                best = (got, c)
    return best


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("block")
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--piece")
    ap.add_argument("--fit", action="store_true")
    ap.add_argument("--set", nargs="*", default=[], metavar="치수=값")
    ap.add_argument("--scale", type=float, help="배율을 직접 줄 때 (pt/inch)")
    ap.add_argument("--origin", type=float, nargs=2)
    ap.add_argument("--aniso", action="store_true", help="가로·세로 배율을 다르게 허용 (기본은 정배율)")
    ap.add_argument("--groups", type=int, default=4, help="맞춰 볼 도면 그림 개수 (큰 것부터)")
    ap.add_argument("--kind", choices=["body", "pants", "sleeve"], help="눈금 종류 (기본: 원형 id 로 판단)")
    ap.add_argument("--no-ruler", action="store_true", help="눈금을 쓰지 않고 형태만으로 맞춘다")
    ap.add_argument("--layer", default="",
                    help="맞출 도면 층 (pattern·developed …). 비우면 전개 층이 있으면 전개, 없으면 패턴")
    ap.add_argument("--cluster", type=int,
                    help="도면 그림 무리 번호 (0 = 가장 큰 것). 한 무리 안에서만 맞춘다")
    ap.add_argument("--min-ox", type=float, help="가로 원점의 하한 (앞판 오른쪽에 뒤판이 오도록 묶어 맞출 때)")
    ap.add_argument("--max-ox", type=float, help="가로 원점의 상한")
    ap.add_argument("--fix-scale", type=float, help="배율을 이 값으로 못박고 자리만 찾는다 (pt/inch)")
    ap.add_argument("--rotate", action="store_true", help="회전까지 맞춘다 (전개 층이면 자동)")
    ap.add_argument("--points", action="store_true", help="도면 꼭짓점을 원형 좌표로 찍어 본다")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    from patterncad.units import parse_inch

    ov = {}
    for kv in a.set:
        k, v = kv.split("=", 1)
        ov[k] = parse_inch(v)
    block = Block.load(ROOT / a.block)
    res = block.evaluate(ov)
    if a.piece:
        res.lines = [l for l in res.lines if l.piece == a.piece]
        used = {n for l in res.lines for n in l.point_names}
        res.points = {k: v for k, v in res.points.items() if k in used}

    doc = pymupdf.open(str(ROOT / "reference" / "portfolio.pdf"))
    page = doc[a.page - 1]
    # 전개(절개-벌림)하는 아이템은 **전개한 뒤가 완성 패턴**이고 그 그림은 전개 층에 있다.
    # 다만 A라인처럼 벌림이 작아 패턴 층 그림이 이미 완성 패턴인 경우도 있어 두 층을 함께 본다.
    hint = block.data.get("verify") or {}
    layers = tuple((a.layer or hint.get("layer") or "pattern,developed").split(","))
    polys = page_polylines(page, layers=layers)
    # 한 장에 몸판·소매·칼라가 같이 있어 엉뚱한 그림에 붙는 장이 있다.
    # 그럴 땐 원형 파일의 verify.cluster 로 어느 그림 무리에 맞출지 못박는다 (0 = 가장 큰 것)
    ci = a.cluster if a.cluster is not None else hint.get("cluster")
    if ci is not None:
        groups = cluster_polylines(polys)
        if ci < len(groups):
            polys = groups[ci]
    cloud = Cloud(polys)
    pts = block_points(res)

    rot = 0.0
    # 전개 조각은 도면에서 돌려 놓여 있으므로 회전까지 맞춘다.
    # 회전은 조각의 무게중심을 축으로 해야 자리를 벗어나지 않는다 → 중심을 원점으로 옮겨 놓고 맞춘다
    # 회전은 도면에서 조각을 돌려 놓은 경우에만 쓴다 (원형 파일의 verify.rotate 로 표시)
    allow_rot = a.rotate or bool((block.data.get("verify") or {}).get("rotate"))
    ctr = Pt(sum(p.x for p in pts) / len(pts), sum(p.y for p in pts) / len(pts))
    if allow_rot:
        pts = [p - ctr for p in pts]

    kind = a.kind
    if not kind:
        bid = block.id
        kind = ("pants" if any(k in bid for k in ("pants", "leggings")) else
                "sleeve" if "sleeve" in bid else "body")
    rf = None if a.no_ruler else ruler_fit(a.page, kind)
    # 눈금 이름표가 조각마다 흩어져 있어 못 믿을 장이 있다 (점프수트 p.71).
    # 그럴 땐 도면에 적힌 치수로 잰 배율을 원형 파일 verify.scale 에 적어 두고 자리만 찾는다
    fixed_s = a.fix_scale or hint.get("scale")
    if fixed_s:
        rf = (float(fixed_s), rf[1] if rf else min(p.y for p in cloud.pts))

    if a.scale and a.origin:
        sx = sy = a.scale
        ox, oy = a.origin
        err = cost(pts, cloud, sx, sy, ox, oy)
    elif rf:
        # 도면 눈금에서 얻은 배율·세로 원점을 기준으로 가로 위치만 찾는다 (가장 믿을 만하다)
        s, oy0 = rf
        px0 = min(p.x for p in cloud.pts)
        px1 = max(p.x for p in cloud.pts)
        bx0 = min(p.x for p in pts)
        cands = []
        x = px0 - 40
        while x < px1 + 40:
            ox0 = x - bx0 * s
            if (a.min_ox is None or ox0 >= a.min_ox) and (a.max_ox is None or ox0 <= a.max_ox):
                cands.append((cost(pts, cloud, s, s, ox0, oy0, cap=30.0), ox0))
            x += 4.0
        cands.sort()
        starts, picked = [], []
        for c, ox0 in cands:                       # 서로 떨어진 후보 몇 개를 고른다
            if any(abs(ox0 - q) < 12 for q in picked):
                continue
            picked.append(ox0)
            starts.append([s, s, ox0, oy0])
            if len(picked) >= 5:
                break
        # 눈금이 조각마다 따로 그려진 장(점프수트 p.71 처럼)에서는 눈금의 세로 원점이
        # 이 조각의 것이 아닐 수 있다. 배율만 눈금에서 받고 자리는 그림 상자에서도 잡아 본다
        bx0b, bx1b = min(p.x for p in pts), max(p.x for p in pts)
        by0b, by1b = min(p.y for p in pts), max(p.y for p in pts)
        for grp in cluster_polylines(polys)[:a.groups]:
            gx0 = min(p.x for pl in grp for p in pl)
            gx1 = max(p.x for pl in grp for p in pl)
            gy0 = min(p.y for pl in grp for p in pl)
            gy1 = max(p.y for pl in grp for p in pl)
            if gx1 - gx0 < 20 or gy1 - gy0 < 20:
                continue
            for ax in (0.0, 0.5, 1.0):
                for ay in (0.0, 0.5, 1.0):
                    oxx = gx0 + (gx1 - gx0) * ax - (bx0b + (bx1b - bx0b) * ax) * s
                    oyy = gy0 + (gy1 - gy0) * ay - (by0b + (by1b - by0b) * ay) * s
                    if a.min_ox is not None and oxx < a.min_ox:
                        continue
                    if a.max_ox is not None and oxx > a.max_ox:
                        continue
                    starts.append([s, s, oxx, oyy])
        starts.sort(key=lambda st: cost(pts, cloud, *st))
        best = None
        lo, hi = (s, s) if fixed_s else (6.0, 30.0)
        for st in starts[:8]:
            got, e = optimise(pts, cloud, st, steps=(3.0, 1.0, 0.35, 0.12, 0.04),
                              uniform=True, rotate=allow_rot, smin=lo, smax=hi)
            if a.min_ox is not None and got[2] < a.min_ox - 1:
                continue
            if a.max_ox is not None and got[2] > a.max_ox + 1:
                continue
            if best is None or e < best[1]:
                best = (got, e)
        if best is None:
            best = (list(starts[0]) + ([0.0] if allow_rot else []), cost(pts, cloud, *starts[0]))
        got, err = best
        sx, sy, ox, oy = got[:4]
        rot = got[4] if len(got) > 4 else 0.0
        if err / s > 0.25 and not fixed_s:
            alt = cluster_align(pts, cloud, polys, a.groups, a.aniso, allow_rot, a.min_ox, a.max_ox)
            if alt and alt[1] / alt[0][0] < err / sx:
                got, err = alt
                sx, sy, ox, oy = got[:4]
                rot = got[4] if len(got) > 4 else 0.0
    else:
        # 눈금이 없는 도면(원형 페이지 등): 그림 단위로 나눠 각각 맞춰 보고 가장 잘 맞는 것을 고른다
        best = cluster_align(pts, cloud, polys, a.groups, a.aniso, allow_rot, a.min_ox, a.max_ox)
        if best is None:
            raise SystemExit(f"p.{a.page} 에서 맞출 만한 도면 그림을 못 찾았다")
        got, err = best
        sx, sy, ox, oy = got[:4]
        rot = got[4] if len(got) > 4 else 0.0

    if allow_rot:
        # 중심을 옮겨 놓고 맞췄으므로 원래 좌표계로 되돌린다
        ox, oy = ox - ctr.x * sx, oy - ctr.y * sy
        if rot:
            res = rotate_resolved(res, rot, ctr)
        pts = block_points(res)

    if not a.quiet:
        print(f"# {block.name} ↔ p.{a.page}{'  ('+a.piece+')' if a.piece else ''}")
        rtxt = f" · 회전 {rot:.1f}°" if rot else ""
        print(f"  배율 가로 {sx:.2f} 세로 {sy:.2f} pt/in · 원점 ({ox:.1f}, {oy:.1f}){rtxt} · 평균오차 {err/sx:.3f}\"")

    if a.fit:
        import yaml

        from verify_block import original_polylines

        fitted = fit_curve_handles(res, original_polylines(page, (sx, sy), (ox, oy), curves_only=True))
        print(yaml.safe_dump({"handles": fitted}, allow_unicode=True, sort_keys=False))
        doc.close()
        return sx, sy, ox, oy, err

    if a.points:
        # 도면의 꼭짓점(선의 끝점·꺾이는 점)을 원형 좌표(인치)로 바꿔 보여 준다 — 치수를 읽으려고
        bx0 = min(p.x for p in pts) - 1.5
        bx1 = max(p.x for p in pts) + 1.5
        by0 = min(p.y for p in pts) - 1.5
        by1 = max(p.y for p in pts) + 1.5
        corners = []
        for pl in polys:
            idxs = [0, len(pl) - 1]
            for i in range(1, len(pl) - 1):
                u = (pl[i] - pl[i - 1]).unit()
                v = (pl[i + 1] - pl[i]).unit()
                if u.dot(v) < 0.94:
                    idxs.append(i)
            for i in idxs:
                q = pl[i]
                corners.append(Pt((q.x - ox) / sx, (q.y - oy) / sy))
        seen = []
        for c in corners:
            if not (bx0 <= c.x <= bx1 and by0 <= c.y <= by1):
                continue
            if any(c.dist(q) < 0.08 for q in seen):
                continue
            seen.append(c)
        print("  도면 꼭짓점 (원형 좌표, 인치):")
        for c in sorted(seen, key=lambda q: (round(q.y, 1), q.x)):
            near = min(res.points.items(), key=lambda kv: kv[1].dist(c))
            d = near[1].dist(c)
            tag = f"  ~ {near[0]} ({d:.2f})" if d < 0.9 else ""
            print(f"    ({c.x:7.3f}, {c.y:7.3f}){tag}")
        doc.close()
        return sx, sy, ox, oy, err

    # 선별 편차
    if not a.quiet:
        print(f"  {'선':<16} {'최대':>6} {'평균':>6}  (인치)")
        for l in res.lines:
            if l.role not in ("outline", "dart"):
                continue
            ds = [cloud.nearest(Pt(p.x * sx + ox, p.y * sy + oy), 20.0) / sx for p in l.polyline(16)]
            mx, mean = max(ds), sum(ds) / len(ds)
            print(f"  {l.name:<16} {mx:6.3f} {mean:6.3f}{' ◀' if mx > 0.2 else ''}")

    idx = None
    for item in json.load(open(ROOT / "extracted" / "index.json", encoding="utf-8")):
        if a.page in item["pages"]:
            idx = item
    # 전개 층까지 보는 경우가 있으니 겹침 그림은 페이지 전체 SVG 위에 그린다
    src = ROOT / idx["dir"] / f"p{a.page:03d}.svg"
    if not src.exists():
        src = ROOT / idx["dir"] / f"p{a.page:03d}_pattern.svg"
    svg = src.read_text(encoding="utf-8")
    overlay = render_group(res, (sx, sy), ox, oy, color="#d22", stroke_w=0.6, labels=False)
    svg = svg.replace("</svg>", f'<g id="rule" opacity="0.9">{overlay}</g></svg>')
    suffix = f"_{a.piece}" if a.piece else ""
    out = ROOT / "verify" / f"{block.id}{suffix}_p{a.page}.svg"
    out.parent.mkdir(exist_ok=True)
    out.write_text(svg, encoding="utf-8")
    d = pymupdf.open(str(out))
    d[0].get_pixmap(dpi=110).save(str(out.with_suffix(".png")))
    d.close()
    doc.close()
    if not a.quiet:
        print(f"  → {out.with_suffix('.png')}")
    return sx, sy, ox, oy, err


if __name__ == "__main__":
    main()
