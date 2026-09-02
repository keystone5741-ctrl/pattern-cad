"""모든 원형을 원본 도면에 자동으로 맞춰 보고 편차를 표로 보고한다.

    python tools/verify_all.py                 # 전부
    python tools/verify_all.py --only jacket   # 이름에 jacket 이 든 것만
"""
import argparse, re, sys, io, contextlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from patterncad.block import Block  # noqa: E402
import align_block  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--only", default="")
ap.add_argument("--pieces", default="앞판,뒤판")
a = ap.parse_args()

rows = []
for f in sorted((ROOT / "blocks").glob("*.yaml")):
    if a.only and a.only not in f.stem:
        continue
    blk = Block.load(f)
    m = re.search(r"p\.(\d+)", str(blk.data.get("source", "")))
    if not m:
        continue
    page = int(m.group(1))
    res = blk.evaluate()
    # 팬츠 원형은 앞판·뒤판을 도면과 같이 나란히 그리므로 통째로 맞춘다.
    # 상의는 한 사각형 안에 겹쳐 그리는데 도면은 따로 그리므로 조각별로 맞춘다.
    pieces = [p for p in a.pieces.split(",") if any(l.piece == p for l in res.lines)]
    if not pieces:
        pieces = [None]
    for pc in pieces:
        argv = [str(f.relative_to(ROOT)), "--page", str(page)]
        if pc:
            argv += ["--piece", pc]
        # 도면은 앞판을 왼쪽, 뒤판을 오른쪽에 그린다 — 뒤판이 앞판 자리로 미끄러지지 않게 막는다
        if pc == "뒤판" and prev_ox is not None:
            argv += ["--min-ox", str(prev_ox)]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                sx, sy, ox, oy, err = align_block.main(argv)
        except Exception as e:  # noqa: BLE001
            rows.append((f.stem, page, pc or "", None, str(e)[:40]))
            continue
        out = buf.getvalue()
        if pc == "앞판":
            xs = [p.x for l in res.lines if l.piece == "앞판" for p in l.polyline(4)]
            fx0, fx1 = min(xs) * sx + ox, max(xs) * sx + ox     # 앞판이 놓인 자리 (pt)
            bxs = [p.x for l in res.lines if l.piece == "뒤판" for p in l.polyline(4)]
            if bxs:
                cb = (min(bxs) + max(bxs)) / 2                  # 뒤판 중심 (원형 좌표)
                prev_ox = fx0 + 0.55 * (fx1 - fx0) - cb * sx
        bad = [l.split()[0] for l in out.splitlines()
               if l.strip().endswith("◀")]
        rows.append((f.stem, page, pc or "", err / sx, ",".join(bad[:6])))

rows.sort(key=lambda r: -(r[3] or 0))
print(f"{'원형':<32}{'p':>4} {'조각':<5}{'평균오차':>8}  큰 편차 선")
for name, page, pc, err, bad in rows:
    e = f"{err:8.3f}" if err is not None else "     err"
    print(f"{name:<32}{page:>4} {pc:<5}{e}  {bad}")
