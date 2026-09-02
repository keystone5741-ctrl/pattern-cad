"""아이템 하나의 도면 정보를 한 번에 훑어본다 (개발용).

    python tools/dump_item.py 13          # p.13 의 선·주석
    python tools/dump_item.py 13 --notes  # 유의사항 본문까지
"""
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]
import pymupdf, extract_portfolio as ex

ap = argparse.ArgumentParser()
ap.add_argument("pages", type=int, nargs="+")
ap.add_argument("--minlen", type=float, default=14)
ap.add_argument("--notes", action="store_true")
a = ap.parse_args()
doc = pymupdf.open(str(ROOT / "reference" / "portfolio.pdf"))
idx = json.load(open(ROOT / "extracted" / "index.json", encoding="utf-8"))
for pno in a.pages:
    page = doc[pno - 1]
    spans = ex.page_spans(page); lines = ex.page_lines(page)
    reg = ex.detect_regions(page, spans, lines, ex.find_title(lines) is not None)
    item = next((i for i in idx if pno in i["pages"]), None)
    print(f"\n########## p{pno}  {item['slug'] if item else '?'} ##########")
    if item and item.get("size"):
        print("  사이즈:", ", ".join(f"{m['name']} {m['body']}→{m['pattern']}" for m in item["size"]["measurements"] if m.get("pattern")))
    segs = []
    for d in page.get_drawings():
        lay = ex.classify_drawing(d, reg)
        if lay not in ("pattern", "developed"):
            continue
        dash = d.get("dashes") not in (None, "[] 0")
        for it in d["items"]:
            if it[0] == "l":
                p1, p2 = it[1], it[2]
                L = ((p2.x - p1.x) ** 2 + (p2.y - p1.y) ** 2) ** 0.5
                if L > a.minlen:
                    segs.append((lay, p1, p2, L, dash))
    print("  --- 선 ---")
    for lay, p1, p2, L, dash in sorted(segs, key=lambda s: (s[0], round(s[1].y), round(s[1].x))):
        k = "H" if abs(p2.y - p1.y) < 1 else ("V" if abs(p2.x - p1.x) < 1 else "D")
        print(f"    [{lay[:4]}] {k} ({p1.x:5.1f},{p1.y:5.1f})-({p2.x:5.1f},{p2.y:5.1f}) len={L:5.1f} {'dash' if dash else ''}")
    print("  --- 주석 ---")
    if item:
        ann = json.load(open(ROOT / item["dir"] / "annotations.json", encoding="utf-8")).get(f"p{pno:03d}", [])
        for i in sorted(ann, key=lambda i: (i["y_pt"], i["x_pt"])):
            print(f"    ({i['x_pt']:5.0f},{i['y_pt']:5.0f}) [{i['layer'][:4]}] {i['text']}")
    if a.notes and item:
        np_ = ROOT / item["dir"] / "notes.md"
        if np_.exists():
            txt = np_.read_text(encoding="utf-8")
            seg = txt.split(f"<!-- p{pno:03d} -->")
            if len(seg) > 1:
                print("  --- 유의사항 ---")
                print("   ", seg[1].split("<!-- p")[0].strip().replace("\n\n", "\n")[:4000])
