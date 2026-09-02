"""원형 규칙 파일을 계산해서 원본 도면(포트폴리오 페이지) 위에 겹쳐 본다.

    python tools/verify_block.py blocks/sichuni_basic.yaml --page 44
        → extracted/.../verify_sichuni_basic.svg / .png  (원본 검정, 규칙 계산 빨강)
        → 선별 편차(인치) 보고

    python tools/verify_block.py blocks/sichuni_basic.yaml --page 44 --fit
        → 곡선 핸들 길이를 원본 폴리라인에 맞춰 구하고 YAML 조각으로 출력

원본 좌표(pt) ↔ 규칙 좌표(인치): pt = origin + inch * scale.
scale 과 origin 은 --scale/--origin 으로 주거나, 기본값(p.44: 24.35 pt/in, (62.2, 118.0))을 쓴다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import extract_portfolio as ex  # noqa: E402
from patterncad.block import Block  # noqa: E402
from patterncad.geometry import Pt, fit_handles, nearest_on_polyline  # noqa: E402
from patterncad.svg import render_group  # noqa: E402


def original_polylines(page, scale: float, origin: tuple[float, float], curves_only: bool = False) -> list[list[Pt]]:
    """패턴 층의 선을 인치 폴리라인 목록으로 (직선은 2점, 베지어는 표본화).
    curves_only=True 면 점선을 빼고, 짧은 조각이 8개 이상 이어진 것(곡선을 잘게 쪼갠 폴리라인)만 돌려준다."""
    spans = ex.page_spans(page)
    lines = ex.page_lines(page)
    reg = ex.detect_regions(page, spans, lines, True)
    ox, oy = origin
    to_in = lambda p: Pt((p.x - ox) / scale, (p.y - oy) / scale)  # noqa: E731
    out = []
    for d in page.get_drawings():
        if ex.classify_drawing(d, reg) != "pattern":
            continue
        dashed = bool(d.get("dashes")) and d.get("dashes") != "[] 0"
        if curves_only and dashed:
            continue
        cur: list[Pt] = []
        for it in d["items"]:
            if it[0] == "l":
                a, b = to_in(it[1]), to_in(it[2])
                if cur and cur[-1].dist(a) < 1e-3:
                    cur.append(b)
                else:
                    if len(cur) > 1:
                        out.append(cur)
                    cur = [a, b]
            elif it[0] == "c":
                from patterncad.geometry import Bezier

                bz = Bezier(to_in(it[1]), to_in(it[2]), to_in(it[3]), to_in(it[4]))
                pts = bz.sample(12)
                if cur and cur[-1].dist(pts[0]) < 1e-3:
                    cur.extend(pts[1:])
                else:
                    if len(cur) > 1:
                        out.append(cur)
                    cur = pts
        if len(cur) > 1:
            out.append(cur)
    if curves_only:
        out = [pl for pl in out if len(pl) >= 8]
    return out


def all_points(polys: list[list[Pt]]) -> list[Pt]:
    return [p for pl in polys for p in pl]


def deviation(line_pts: list[Pt], polys: list[list[Pt]]) -> tuple[float, float]:
    """규칙 선의 표본점 → 원본 선까지 거리의 (최대, 평균)."""
    ds = []
    for p in line_pts:
        best = min(nearest_on_polyline(p, pl)[0] for pl in polys)
        ds.append(best)
    return max(ds), sum(ds) / len(ds)


def fit_curve_handles(res, polys, tol=0.3):
    """곡선 선마다 구간별 핸들 길이를 원본 폴리라인에 맞춘다. {선이름: [[h0,h3], ...]}"""
    pts_all = all_points(polys)
    result = {}
    for line in res.lines:
        if line.kind != "curve":
            continue
        handles = []
        for i, bz in enumerate(line.beziers):
            p0, p3 = bz.p0, bz.p3
            t0 = (bz.c1 - bz.p0).unit()
            t3 = (bz.p3 - bz.c2).unit()
            chord = p3 - p0
            L = chord.length()
            cur = bz
            h = (1 / 3, 1 / 3)
            for _ in range(3):  # 표본 선택 ↔ 맞춤 반복
                curve_pts = cur.sample(48)
                samples = []
                for p in pts_all:
                    u = (p - p0).dot(chord) / (L * L)
                    if -0.02 <= u <= 1.02 and nearest_on_polyline(p, curve_pts)[0] < tol:
                        samples.append((u, p))
                samples.sort(key=lambda s: s[0])
                if len(samples) < 4:
                    break
                h = fit_handles(p0, t0, p3, t3, [p0] + [s[1] for s in samples] + [p3])
                from patterncad.geometry import bezier_from_tangents

                cur = bezier_from_tangents(p0, t0, p3, t3, h[0], h[1])
            handles.append([round(h[0], 3), round(h[1], 3)])
        result[line.name] = handles
    return result


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("block")
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--scale", type=float, default=24.35, help="pt per inch")
    ap.add_argument("--origin", type=float, nargs=2, default=(62.2, 118.0), help="원점의 pt 좌표")
    ap.add_argument("--fit", action="store_true")
    ap.add_argument("--out", help="출력 SVG 경로 (기본: 원형 파일 옆 verify_<id>.svg)")
    ap.add_argument("--set", nargs="*", default=[], metavar="치수=값",
                    help="치수 임시 덮어쓰기 (도면이 표기와 다를 때 실측값으로 형태만 검증)")
    args = ap.parse_args(argv)

    from patterncad.units import parse_inch

    overrides = {}
    for kv in args.set:
        k, v = kv.split("=", 1)
        overrides[k] = parse_inch(v)
    block = Block.load(args.block)
    res = block.evaluate(overrides)
    if overrides:
        print("임시 치수:", overrides)
    doc = pymupdf.open(str(ROOT / "reference" / "portfolio.pdf"))
    page = doc[args.page - 1]
    polys = original_polylines(page, args.scale, tuple(args.origin))

    if args.fit:
        import yaml

        fitted = fit_curve_handles(res, original_polylines(page, args.scale, tuple(args.origin), curves_only=True))
        print(yaml.safe_dump({"handles": fitted}, allow_unicode=True, sort_keys=False))
        return

    # 편차 보고
    print(f"{'선':<14} {'최대':>6} {'평균':>6}  (인치)")
    worst = []
    for line in res.lines:
        if line.role not in ("outline", "dart"):
            continue
        mx, mean = deviation(line.polyline(16), polys)
        worst.append((mx, line.name))
        flag = " ◀" if mx > 0.15 else ""
        print(f"{line.name:<14} {mx:6.3f} {mean:6.3f}{flag}")

    # 겹침 SVG: 원본 패턴층 SVG 안에 규칙 그림(빨강)을 끼워 넣는다
    idx = None
    for item in __import__("json").load(open(ROOT / "extracted" / "index.json", encoding="utf-8")):
        if args.page in item["pages"]:
            idx = item
    src = ROOT / idx["dir"] / f"p{args.page:03d}_pattern.svg"
    svg = src.read_text(encoding="utf-8")
    overlay = render_group(res, args.scale, args.origin[0], args.origin[1], color="#d22", stroke_w=0.6)
    svg = svg.replace("</svg>", f'<g id="rule" opacity="0.85">{overlay}</g></svg>')
    out = Path(args.out) if args.out else Path(args.block).with_name(f"verify_{block.id}.svg")
    out.write_text(svg, encoding="utf-8")
    png = out.with_suffix(".png")
    d = pymupdf.open(str(out))
    d[0].get_pixmap(dpi=150).save(str(png))
    print(f"\n→ {out}\n→ {png}")


if __name__ == "__main__":
    main()
