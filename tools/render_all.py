"""모든 원형과 스타일을 실물 크기 SVG 로 다시 그린다 (검토용).

    python tools/render_all.py            # blocks/*.svg, styles/*.svg
    python tools/render_all.py --png      # 미리보기 PNG 도 함께
"""
import argparse, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from patterncad.block import Block
from patterncad.style import Style
from patterncad.svg import render_pieces_svg, render_svg, render_style_svg

ap = argparse.ArgumentParser()
ap.add_argument("--png", action="store_true")
ap.add_argument("--dpi", type=int, default=45)
a = ap.parse_args()

def png(svg_path):
    import pymupdf
    doc = pymupdf.open(stream=svg_path.read_bytes(), filetype="svg")
    doc[0].get_pixmap(dpi=a.dpi).save(str(svg_path.with_suffix(".png")))

n = 0
for f in sorted((ROOT / "blocks").glob("*.yaml")):
    res = Block.load(f).evaluate()
    pieces = {l.piece for l in res.lines if l.piece}
    out = f.with_suffix(".svg")
    out.write_text(render_pieces_svg(res) if len(pieces) > 1 else render_svg(res), encoding="utf-8")
    if a.png:
        png(out)
    n += 1
m = 0
for f in sorted((ROOT / "styles").glob("*.yaml")):
    res = Style.load(f).evaluate()
    out = f.with_suffix(".svg")
    out.write_text(render_style_svg(res, labels=False), encoding="utf-8")
    if a.png:
        png(out)
    m += 1
print(f"원형 {n} · 스타일 {m} 그림 완료")
