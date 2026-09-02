"""곡선 핸들을 원본 도면에 맞춰 원형 파일의 handles: 칸에 써 넣는다.

    python tools/fit_all.py                 # 도면 페이지가 적힌 원형 전부
    python tools/fit_all.py --only jacket   # 이름으로 골라서
    python tools/fit_all.py --dry           # 파일은 건드리지 않고 보고만

각 조각(앞판·뒤판)을 도면에 맞춰 놓고, 곡선 구간마다 핸들 길이를 최소제곱으로 맞춘다.
**맞춘 뒤 편차가 실제로 줄어든 선만** 저장한다.
"""
import argparse, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]
import pymupdf  # noqa: E402
import align_block as ab  # noqa: E402
from patterncad.block import Block  # noqa: E402
from patterncad.geometry import Pt  # noqa: E402
from verify_block import fit_curve_handles, original_polylines  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--only", default="")
ap.add_argument("--dry", action="store_true")
ap.add_argument("--max-err", type=float, default=0.30, help="이보다 못 맞은 조각은 건너뛴다 (인치)")
a = ap.parse_args()

doc = pymupdf.open(str(ROOT / "reference" / "portfolio.pdf"))


def line_dev(res, name, cloud, sx, sy, ox, oy):
    for l in res.lines:
        if l.name == name:
            ds = [cloud.nearest(Pt(p.x * sx + ox, p.y * sy + oy), 30.0) / sx for p in l.polyline(24)]
            return sum(ds) / len(ds)
    return None


for f in sorted((ROOT / "blocks").glob("*.yaml")):
    if a.only and a.only not in f.stem:
        continue
    text = f.read_text(encoding="utf-8")
    m = re.search(r"^source:.*p\.(\d+)", text, re.M)
    if not m:
        continue
    page_no = int(m.group(1))
    blk = Block.load(f)
    base = blk.evaluate()
    pieces = []
    for l in base.lines:
        if l.piece and l.piece not in pieces:
            pieces.append(l.piece)
    pieces = pieces or [None]

    page = doc[page_no - 1]
    polys_pt = ab.page_polylines(page)
    cloud = ab.Cloud(polys_pt)
    kept: dict[str, list] = {}
    for pc in pieces:
        argv = [str(f.relative_to(ROOT)), "--page", str(page_no), "--quiet"]
        if pc:
            argv += ["--piece", pc]
        try:
            sx, sy, ox, oy, err = ab.main(argv)
        except Exception as e:  # noqa: BLE001
            print(f"{f.stem:32} {pc or '':5} 맞춤 실패: {e}")
            continue
        if err / sx > a.max_err:
            print(f"{f.stem:32} {pc or '':5} 건너뜀 (평균오차 {err/sx:.2f}\")")
            continue
        res = blk.evaluate()
        # 외곽선만 맞춘다 — 표시선(사이바·주머니 등)은 도면의 다른 선을 잘못 잡을 수 있다
        res.lines = [l for l in res.lines if l.role == "outline" and (not pc or l.piece == pc)]
        fitted = fit_curve_handles(res, original_polylines(page, (sx, sy), (ox, oy), curves_only=True))
        for name, hs in fitted.items():
            if any(h is None for h in hs):
                continue
            if any(not (0.2 <= h[0] <= 0.8 and 0.2 <= h[1] <= 0.8) for h in hs):
                continue
            before = line_dev(res, name, cloud, sx, sy, ox, oy)
            ld = next(l for l in blk.lines if l.name == name)
            old = ld.handles
            ld.handles = hs
            after_res = blk.evaluate()
            after = line_dev(after_res, name, cloud, sx, sy, ox, oy)
            ld.handles = old
            if after is not None and before is not None and after < before - 0.015:
                kept[name] = hs
                print(f"{f.stem:32} {pc or '':5} {name:<12} {before:.3f} → {after:.3f}")
    if not kept or a.dry:
        continue
    body = "\n".join(
        f"  {n}: [" + ", ".join(f"[{h[0]:.3f}, {h[1]:.3f}]" for h in hs) + "]"
        for n, hs in kept.items())
    block = ("\nhandles:   # 곡선 모양을 원본 도면에 맞춘 값 — tools/fit_all.py\n" + body + "\n")
    i = text.find("\nhandles:")
    if i >= 0:
        text = text[:i] + block
    else:
        text = text.rstrip("\n") + "\n" + block
    f.write_text(text, encoding="utf-8")
print("끝")
