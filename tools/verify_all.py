"""모든 원형을 원본 도면에 자동으로 맞춰 보고 편차를 표로 보고한다.

    python tools/verify_all.py                 # 전부
    python tools/verify_all.py --only jacket   # 이름에 jacket 이 든 것만
    python tools/verify_all.py --jobs 4        # 원형 4 개를 한꺼번에 (기본: 코어 수)

도면은 앞판을 왼쪽, 뒤판을 오른쪽에 그린다. 조각 하나를 따로 맞추면 옆에 그린 소매·칼라
그림으로 미끄러지는 일이 있어서, 두 조각을 다 맞춘 뒤 좌우 순서가 뒤바뀌었으면
'앞판은 뒤판보다 왼쪽' 이라는 조건을 주고 다시 맞춘다.
"""
import argparse, re, sys, io, contextlib, os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from patterncad.block import Block  # noqa: E402


def align(path, page, pc, extra=()):
    """조각 하나를 맞춘다 → (sx, sy, ox, oy, err, 큰편차선) 또는 (None, 사유)."""
    import align_block  # noqa: PLC0415

    argv = [path, "--page", str(page)] + (["--piece", pc] if pc else []) + list(extra)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            got = align_block.main(argv)
    except (Exception, SystemExit) as e:  # noqa: BLE001
        return None, str(e)[:40]
    bad = [l.split()[0] for l in buf.getvalue().splitlines() if l.strip().endswith("◀")]
    return got, bad


def span(res, pc, sx, ox):
    xs = [p.x for l in res.lines if l.piece == pc for p in l.polyline(4)]
    return min(xs) * sx + ox, max(xs) * sx + ox


def one_block(args):
    """원형 하나를 조각별로 도면에 맞춰 본다 (딴 프로세스에서 돌린다)."""
    import align_block  # noqa: PLC0415

    path, page, pieces = args
    blk = Block.load(ROOT / path)
    res = blk.evaluate()
    kind = ((blk.data.get("verify") or {}).get("kind") or
            ("pants" if any(k in blk.id for k in ("pants", "leggings")) else
             "sleeve" if any(l.piece == "소매" for l in res.lines) else "body"))
    rf = align_block.ruler_fit(page, kind)

    fits = {pc: align(path, page, pc) for pc in pieces}
    # 한 장에 그린 두 조각은 같은 배율이어야 한다. 배율이 5% 넘게 어긋나면 한 조각이
    # 엉뚱한 그림(소매 등)에 앉은 것이므로, 눈금 배율에 가까운 쪽 배율로 못박고 다시 맞춘다.
    if fits.get("앞판", (None,))[0] and fits.get("뒤판", (None,))[0] and rf:
        sf, sb = fits["앞판"][0][0], fits["뒤판"][0][0]
        if abs(sf / sb - 1) > 0.05:
            good, bad_pc = ("앞판", "뒤판") if abs(sf / rf[0] - 1) < abs(sb / rf[0] - 1) else ("뒤판", "앞판")
            fits[bad_pc] = align(path, page, bad_pc, ["--fix-scale", str(fits[good][0][0])])
    # 도면은 앞판을 왼쪽, 뒤판을 오른쪽에 그린다 — 좌우가 뒤바뀌었으면 뒤판만 다시 맞춘다
    if fits.get("앞판", (None,))[0] and fits.get("뒤판", (None,))[0]:
        fx = span(res, "앞판", fits["앞판"][0][0], fits["앞판"][0][2])
        bx = span(res, "뒤판", fits["뒤판"][0][0], fits["뒤판"][0][2])
        if fx[0] > bx[0]:
            cb = (min(p.x for l in res.lines if l.piece == "뒤판" for p in l.polyline(4)) +
                  max(p.x for l in res.lines if l.piece == "뒤판" for p in l.polyline(4))) / 2
            fits["뒤판"] = align(path, page, "뒤판",
                                ["--min-ox", str(fx[0] + 0.55 * (fx[1] - fx[0])
                                                 - cb * fits["앞판"][0][0])])

    rows = []
    for pc in pieces:
        got, bad = fits[pc]
        if got is None:
            rows.append((Path(path).stem, page, pc or "", None, bad, None))
            continue
        sx, sy, ox, oy, err = got
        # 눈금에서 얻은 배율과 실제로 맞은 배율의 비 — 1 에서 멀면 원형 크기(기장 등)가 도면과 다르다
        rows.append((Path(path).stem, page, pc or "", err / sx, ",".join(bad[:6]),
                     sx / rf[0] if rf else None))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--pieces", default="앞판,뒤판")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    a = ap.parse_args()

    jobs = []
    for f in sorted((ROOT / "blocks").glob("*.yaml")):
        if a.only and a.only not in f.stem:
            continue
        blk = Block.load(f)
        m = re.search(r"p\.(\d+)", str(blk.data.get("source", "")))
        if not m or (blk.data.get("verify") or {}).get("skip"):
            continue
        res = blk.evaluate()
        pieces = [p for p in a.pieces.split(",") if any(l.piece == p for l in res.lines)] or [None]
        jobs.append((str(f.relative_to(ROOT)), int(m.group(1)), pieces))

    rows = []
    with ProcessPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        for got in ex.map(one_block, jobs):
            rows += got
            for r in got:
                e = f"{r[3]:.3f}" if r[3] is not None else "err"
                print(f"  … {r[0]} {r[2]} {e}", file=sys.stderr, flush=True)

    rows.sort(key=lambda r: -(r[3] or 0))
    print(f"{'원형':<32}{'p':>4} {'조각':<5}{'평균오차':>8} {'크기비':>6}  큰 편차 선")
    for name, page, pc, err, bad, ratio in rows:
        e = f"{err:8.3f}" if err is not None else "     err"
        r = f"{ratio:6.2f}" if ratio else "     -"
        print(f"{name:<32}{page:>4} {pc:<5}{e} {r}  {bad}")


if __name__ == "__main__":
    main()
