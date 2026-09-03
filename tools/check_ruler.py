"""원형에 적은 치수(진동깊이·등길이·기장 …)가 도면 눈금 표기와 같은지 대조한다.

    python tools/check_ruler.py
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import extract_portfolio as ex  # noqa: E402
import align_block as ab  # noqa: E402
from patterncad.block import Block  # noqa: E402
from patterncad.units import to_fraction  # noqa: E402

IDX = json.load(open(ROOT / "extracted" / "index.json", encoding="utf-8"))
NAMES = set(ab.RULER_BODY + ab.RULER_PANTS + ab.RULER_SLEEVE)


def labels(pg):
    """도면 눈금 이름표와 그 아래 값을 짝지어 돌려 준다."""
    item = next((i for i in IDX if pg in i["pages"]), None)
    if not item:
        return {}
    ann = json.load(open(ROOT / item["dir"] / "annotations.json", encoding="utf-8")).get(f"p{pg:03d}", [])
    out = {}
    for a1 in ann:
        t = a1["text"].strip()
        if t not in NAMES:
            continue
        for a2 in ann:
            if a2 is a1 or not (0 < a2["y_pt"] - a1["y_pt"] < 14):
                continue
            if abs(a2["x_pt"] - a1["x_pt"]) > 24:
                continue
            v = ex.parse_inch(a2["text"])
            if v:
                out.setdefault(t, []).append(v)
                break
    return out


def main():
    bad = 0
    for f in sorted((ROOT / "blocks").glob("*.yaml")):
        blk = Block.load(f)
        m = re.search(r"p\.(\d+)", str(blk.data.get("source", "")))
        if not m:
            continue
        lab = labels(int(m.group(1)))
        if not lab:
            continue
        ms = blk.evaluate().measurements
        diffs = []
        for k, vs in lab.items():
            if k not in ms:
                continue
            mine = ms[k]
            if not any(abs(mine - v) < 1e-6 for v in vs):
                diffs.append(f"{k} 원형 {to_fraction(mine)} ≠ 도면 " +
                             "/".join(to_fraction(v) for v in vs))
        if diffs:
            bad += 1
            print(f"{f.stem:<30} p{m.group(1):>3}  " + " | ".join(diffs))
    print(f"— 표기와 다른 원형 {bad} 개")


if __name__ == "__main__":
    main()
