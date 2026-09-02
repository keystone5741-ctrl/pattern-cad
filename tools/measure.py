"""원형을 계산해 점 좌표·선 길이·점 사이 거리를 찍어 본다 (개발·검증용).

    python tools/measure.py blocks/pants_basic.yaml
    python tools/measure.py blocks/pants_basic.yaml --pt F_W_CF F_W_SS --dist F_W_CF F_W_SS --line 앞옆선
    python tools/measure.py blocks/pants_basic.yaml --set 뒤중심각=5 --svg
"""
import argparse, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from patterncad.block import Block
from patterncad.svg import render_pieces_svg, render_svg
from patterncad.units import to_fraction

ap = argparse.ArgumentParser()
ap.add_argument("block")
ap.add_argument("--set", action="append", default=[], metavar="치수=값")
ap.add_argument("--pt", nargs="*", default=[])
ap.add_argument("--dist", nargs=2, action="append", default=[])
ap.add_argument("--line", nargs="*", default=[])
ap.add_argument("--meas", nargs="*", default=[])
ap.add_argument("--all-lines", action="store_true")
ap.add_argument("--svg", action="store_true", help="blocks/<id>.svg 다시 그린다")
a = ap.parse_args()

ov = {}
for s in a.set:
    k, v = s.split("=", 1)
    ov[k] = float(v) if v.replace(".", "").replace("-", "").isdigit() else v
blk = Block.load(ROOT / a.block)
res = blk.evaluate(ov)
print(f"# {blk.name} ({blk.id})")
for n in a.meas:
    v = res.measurements[n]
    print(f"  치수 {n} = {v if isinstance(v, str) else f'{v:.4f}  {to_fraction(v, 16)}'}")
for n in a.pt:
    p = res.points[n]
    print(f"  점 {n} = ({p.x:.4f}, {p.y:.4f})")
for x, y in a.dist:
    d = (res.points[x] - res.points[y]).length()
    print(f"  거리 {x}~{y} = {d:.4f}  {to_fraction(d, 16)}")
names = [l.name for l in res.lines] if a.all_lines else a.line
for n in names:
    for l in res.lines:
        if l.name == n:
            print(f"  선 {n}{' ('+l.piece+')' if l.piece else ''} = {l.length():.4f}  {to_fraction(l.length(), 16)}")
if a.svg:
    out = ROOT / "blocks" / f"{blk.id}.svg"
    pieces = {l.piece for l in res.lines if l.piece}
    out.write_text(render_pieces_svg(res) if len(pieces) > 1 else render_svg(res), encoding="utf-8")
    print(f"  → {out}")
