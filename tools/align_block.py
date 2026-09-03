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


RULER_BODY = ["진동깊이", "등길이", "엉덩이길이", "무릎길이", "기장"]
FROM_WAIST = {"엉덩이길이", "무릎길이"}   # 이 둘만 허리선에서 잰다
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
        if groups and f[3] - groups[-1][-1][3] < 40:
            groups[-1].append(f)
        else:
            groups.append([f])
    anchor = {"body": ("등길이", "진동깊이"), "pants": ("밑위길이", "기장"),
              "sleeve": ("소매산높이", "소매길이")}[kind]
    named = [g for g in groups if any(x[0] in anchor for x in g)]
    grp = max(named or groups, key=len)
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
    ap.add_argument("--min-ox", type=float, help="가로 원점의 하한 (앞판 오른쪽에 뒤판이 오도록 묶어 맞출 때)")
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
            if a.min_ox is None or ox0 >= a.min_ox:
                cands.append((cost(pts, cloud, s, s, ox0, oy0, cap=30.0), ox0))
            x += 4.0
        cands.sort()
        picked, best = [], None
        for c, ox0 in cands:                       # 서로 떨어진 후보 몇 개에서 각각 다듬는다
            if any(abs(ox0 - q) < 12 for q in picked):
                continue
            picked.append(ox0)
            got, e = optimise(pts, cloud, [s, s, ox0, oy0], steps=(3.0, 1.0, 0.35, 0.12, 0.04),
                              uniform=True, rotate=allow_rot)
            if a.min_ox is not None and got[2] < a.min_ox - 1:
                continue
            if best is None or e < best[1]:
                best = (got, e)
            if len(picked) >= 5:
                break
        got, err = best
        sx, sy, ox, oy = got[:4]
        rot = got[4] if len(got) > 4 else 0.0
        if err / s > 0.45 and not a.min_ox:
            try:
                alt = main([a.block, "--page", str(a.page), "--no-ruler", "--quiet"] +
                           (["--piece", a.piece] if a.piece else []))
                if alt and alt[4] / alt[0] < err / s:
                    sx, sy, ox, oy, err = alt[:5]
                    rot = 0.0
            except Exception:  # noqa: BLE001
                pass
    else:
        # 눈금이 없는 도면(원형 페이지 등): 그림 단위로 나눠 각각 맞춰 보고 가장 잘 맞는 것을 고른다
        bx0, bx1 = min(p.x for p in pts), max(p.x for p in pts)
        by0, by1 = min(p.y for p in pts), max(p.y for p in pts)
        best = None
        for grp in cluster_polylines(polys)[:a.groups]:
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
                    if (not a.aniso):
                        sxx = syy = (sxx + syy) / 2
                    for ax in (0.0, 0.5, 1.0):
                        for ay in (0.0, 0.5, 1.0):
                            oxx = px0 + (px1 - px0) * ax - (bx0 + (bx1 - bx0) * ax) * sxx
                            oyy = py0 + (py1 - py0) * ay - (by0 + (by1 - by0) * ay) * syy
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
                got, c = optimise(pts, cloud, s, uniform=(not a.aniso), rotate=allow_rot)
                # 원형을 빽빽한 곳에 쪼그려 넣으면 오차가 작게 나온다.
                # 놓인 크기가 그림 크기와 비슷할 때만 인정한다
                area = (bx1 - bx0) * got[0] * (by1 - by0) * got[1]
                if not (0.4 <= area / grp_area <= 2.5):
                    continue
                if best is None or c < best[1]:
                    best = (got, c)
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
    if not a.quiet:
        print(f"  → {out.with_suffix('.png')}")
    return sx, sy, ox, oy, err


if __name__ == "__main__":
    main()
