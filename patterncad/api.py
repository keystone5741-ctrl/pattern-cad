"""화면(ui/)과 엔진 사이의 JSON.

서버(server.py)가 부른다. 계산 결과(Resolved)를 점·선·치수 표로 풀고, 조각(piece)별로
겹치지 않게 나란히 놓을 자리(dx, dy)를 정한다. 좌표는 전부 인치. 화면이 단위를 바꿔 보여 준다.
"""

from __future__ import annotations

from pathlib import Path

import yaml

import json
import re
import sys
import threading

from .block import Block, Resolved
from .compose import availability, category_of, compose
from .dxf import write_dxf
from .grading import size_overrides, systems
from .hpgl import write_hpgl
from .pieces import build_pieces, transform_piece
from .style import Style
from .svg import render_pieces_svg, render_style_svg, render_svg
from .units import parse_inch

ROOT = Path(__file__).resolve().parent.parent
GAP_IN = 2.0


def catalog() -> dict:
    """스타일·원형 목록. 파일 머리말만 읽는다."""
    def head(path):
        with open(path, encoding="utf-8") as f:
            d = yaml.safe_load(f) or {}
        return {"id": d.get("id", path.stem), "name": d.get("name", path.stem), "file": path.name,
                "category": d.get("category", ""), "source": d.get("source", "")}
    styles = [head(p) for p in sorted((ROOT / "styles").glob("*.yaml"))]
    blocks = [head(p) for p in sorted((ROOT / "blocks").glob("*.yaml"))]
    return {"styles": styles, "blocks": blocks}


# ------------------------------------------------------------------ 규칙을 글로
def _v(x):
    return str(x)


def describe_rule(rule: dict) -> str:
    """점 규칙(dict)을 사람이 읽는 한 줄로. 식은 그대로 보여 준다 — 그게 규칙의 핵심이다."""
    if "at" in rule:
        x, y = rule["at"]
        return f"좌표 ({_v(x)}, {_v(y)})"
    if "midpoint" in rule:
        a, b = rule["midpoint"]
        return f"{a} · {b} 의 중점"
    if "along" in rule:
        a, b = rule["along"]
        if "ratio" in rule:
            return f"{a} → {b} 의 {_v(rule['ratio'])} 지점"
        return f"{a} → {b} 선 위, {a} 에서 {_v(rule['dist'])}"
    if "intersect" in rule:
        (a, b), (c, d) = rule["intersect"]
        return f"{a}–{b} 와 {c}–{d} 의 교점"
    if "foot" in rule:
        a, b = rule["foot"]["line"]
        return f"{rule['foot']['of']} 에서 {a}–{b} 에 내린 수선의 발"
    if "perp" in rule:
        r = rule["perp"]
        a, b = r["line"]
        return f"{r['from']} 에서 {a}–{b} 에 수직으로 {_v(r['dist'])}"
    if "rotate" in rule:
        r = rule["rotate"]
        return f"{r['of']} 를 {r['center']} 중심으로 {_v(r['angle'])}° 회전"
    if "mirror" in rule:
        r = rule["mirror"]
        a, b = r["line"]
        return f"{r['of']} 를 {a}–{b} 에 대칭"
    if "polar" in rule:
        r = rule["polar"]
        return f"{r['center']} 에서 반지름 {_v(r['radius'])}, 각도 {_v(r['angle'])}°"
    if "circle" in rule:
        c = rule["circle"]
        if "x" in rule:
            return f"{c['center']} 중심 반지름 {_v(c['radius'])} 원 위, x = {_v(rule['x'])} ({rule.get('side', 'down')})"
        return f"{c['center']} 중심 반지름 {_v(c['radius'])} 원 위, y = {_v(rule['y'])} ({rule.get('side', 'right')})"
    if "from" in rule:
        if "dir" in rule:
            dx, dy = rule["dir"]
            return f"{rule['from']} 에서 [{_v(dx)}, {_v(dy)}] 방향으로 {_v(rule['dist'])}"
        parts = []
        if rule.get("dx", 0) not in (0, "0"):
            parts.append(f"가로 {_v(rule['dx'])}")
        if rule.get("dy", 0) not in (0, "0"):
            parts.append(f"세로 {_v(rule['dy'])}")
        return f"{rule['from']} 에서 " + (", ".join(parts) if parts else "그대로")
    return str(rule)


# ------------------------------------------------------------------ 치수 표
def _is_linked(v) -> bool:
    """스타일 파일이 다른 원형의 값으로 묶어 둔 치수인가 (예: 앞AH: "len(body.앞암홀)")."""
    return isinstance(v, str) and parse_inch(v) is None and any(c in v for c in "(.+-*/")


def measurement_rows(block: Block, res: Resolved, style_meas: dict, user_ov: dict) -> list[dict]:
    rows = []
    for name, spec in block.measurements.items():
        spec = spec if isinstance(spec, dict) else {"value": spec}
        kind = next((k for k in ("choice", "table", "formula", "value") if k in spec), "value")
        row = {"name": name, "ko": spec.get("ko", ""), "note": spec.get("note", ""), "kind": kind,
               "value": res.measurements.get(name), "modified": name in user_ov}
        if kind == "choice":
            row["options"] = list(spec.get("options") or [])
        elif kind == "table":
            row["formula"] = f"{spec.get('key')} 에 따라 " + ", ".join(f"{k}: {v}" for k, v in spec["table"].items())
        elif kind == "formula":
            row["formula"] = str(spec["formula"])
        sv = style_meas.get(name)
        if _is_linked(sv):
            row["linked"] = sv
            row["editable"] = False
        else:
            row["editable"] = kind in ("value", "choice")
            if sv is not None and not row["modified"]:
                row["style_value"] = sv
        rows.append(row)
    return rows


# ------------------------------------------------------------------ 조각 배치
def _piece_names(res: Resolved) -> list[str]:
    names = []
    for l in res.lines:
        pc = l.piece or ""
        if pc not in names:
            names.append(pc)
    return names or [""]


def _bbox(lines) -> tuple:
    xs, ys = [], []
    for l in lines:
        for p in l.polyline(8):
            xs.append(p.x)
            ys.append(p.y)
    if not xs:
        return (0, 0, 0, 0)
    return (min(xs), min(ys), max(xs), max(ys))


def _overlap(a, b, tol=0.25) -> bool:
    return a[0] < b[2] - tol and b[0] < a[2] - tol and a[1] < b[3] - tol and b[1] < a[3] - tol


def layout(results: dict[str, Resolved]) -> list[dict]:
    """모든 원형을 왼쪽부터 한 줄로.

    원형 안의 조각(앞판·뒤판…)은 원형 좌표 그대로 둔다 — 시추니처럼 앞·뒤판이 이미 나란히 그려진
    원형이 대부분이고, 안내선(조각 없는 선)도 그 자리에 있어야 뜻이 통한다.
    전개해서 조각끼리 겹치는 원형만 조각별로 떼어 놓는다."""
    out = []
    x = 0.0
    for key, res in results.items():
        names = _piece_names(res)
        named = [n for n in names if n]
        boxes = {n: _bbox([l for l in res.lines if (l.piece or "") == n]) for n in names}
        spread = any(_overlap(boxes[a], boxes[b]) for i, a in enumerate(named) for b in named[i + 1:])
        if not spread:
            x0, y0, x1, y1 = _bbox(res.lines)
            for n in names:
                bx = boxes[n]
                out.append({"block": key, "piece": n, "dx": x - x0, "dy": -y0,
                            "bbox": list(bx), "w": bx[2] - bx[0], "h": bx[3] - bx[1]})
            x += (x1 - x0) + GAP_IN
            continue
        first_dx = None
        for n in named:
            x0, y0, x1, y1 = boxes[n]
            out.append({"block": key, "piece": n, "dx": x - x0, "dy": -y0,
                        "bbox": [x0, y0, x1, y1], "w": x1 - x0, "h": y1 - y0})
            if first_dx is None:
                first_dx = (x - x0, -y0)
            x += (x1 - x0) + GAP_IN * 0.75
        if "" in names:  # 조각 없는 안내선은 첫 조각 자리에
            bx = boxes[""]
            out.append({"block": key, "piece": "", "dx": first_dx[0], "dy": first_dx[1],
                        "bbox": list(bx), "w": bx[2] - bx[0], "h": bx[3] - bx[1]})
        x += GAP_IN * 0.5
    return out


# ------------------------------------------------------------------ 전체
def _split(prefix_map: dict, key: str) -> dict:
    return {k[len(key) + 1:]: v for k, v in prefix_map.items() if k.startswith(key + ".")}


def load_style(ident: str, compose_choices: dict | None = None) -> Style:
    st = Style.load(ROOT / "styles" / f"{ident}.yaml")
    return compose(st, compose_choices) if compose_choices else st


def _evaluate(kind: str, ident: str, overrides: dict, point_overrides: dict, line_overrides: dict | None = None,
              compose_choices: dict | None = None):
    """(결과 dict, 스타일별 치수 지정, 이름). block 은 'block' 키 하나로 감싼다. compose_choices 는 디테일 옵션."""
    line_overrides = line_overrides or {}
    if kind == "style":
        st = load_style(ident, compose_choices)
        results = st.evaluate(overrides, point_overrides, line_overrides)
        style_meas = {name: meas for name, _, meas in st.blocks}
        return results, style_meas, st.name
    blk = Block.load(ROOT / "blocks" / f"{ident}.yaml")
    res = blk.evaluate(_split(overrides, "block"), _split(point_overrides, "block"), _split(line_overrides, "block"))
    return {"block": res}, {"block": {}}, blk.name


def to_json(kind: str, ident: str, overrides: dict | None = None, point_overrides: dict | None = None,
            line_overrides: dict | None = None, compose_choices: dict | None = None) -> dict:
    overrides = overrides or {}
    point_overrides = point_overrides or {}
    results, style_meas, name = _evaluate(kind, ident, overrides, point_overrides, line_overrides, compose_choices)
    pieces = layout(results)
    blocks = []
    for key, res in results.items():
        pcs = [p for p in pieces if p["block"] == key]
        # 점이 어느 조각에 속하는지: 그 점을 쓰는 첫 선의 조각. 어디에도 안 쓰이면 첫 조각
        owner = {}
        for l in res.lines:
            for n in l.point_names:
                owner.setdefault(n, l.piece or "")
        first_pc = pcs[0]["piece"] if pcs else ""
        points = []
        for n, p in res.points.items():
            meta = res.point_meta.get(n, {})
            row = {"name": n, "x": p.x, "y": p.y, "ko": meta.get("ko", ""), "note": meta.get("note", ""),
                   "piece": owner.get(n, first_pc), "rule": describe_rule(meta.get("rule", {})),
                   "override": bool(meta.get("override"))}
            if "computed" in meta:
                row["computed"] = list(meta["computed"])
            points.append(row)
        lines = []
        for l in res.lines:
            lines.append({"name": l.name, "role": l.role, "kind": l.kind, "piece": l.piece or "",
                          "points": list(l.point_names), "pts": [[p.x, p.y] for p in l.pts],
                          "beziers": [[[b.p0.x, b.p0.y], [b.c1.x, b.c1.y], [b.c2.x, b.c2.y], [b.p3.x, b.p3.y]] for b in l.beziers],
                          "ko": l.ko, "length": l.length(),
                          "overridden": sorted(int(k) for k in l.overrides)})
        blocks.append({"key": key, "id": res.block.id, "name": res.block.name, "category": res.block.category,
                       "source": res.block.data.get("source", ""), "extends": res.block.data.get("extends_from"),
                       "pieces": [p["piece"] for p in pcs],
                       "measurements": measurement_rows(res.block, res, style_meas.get(key, {}), _split(overrides, key)),
                       "points": points, "lines": lines})
    return {"kind": kind, "id": ident, "name": name, "blocks": blocks, "pieces": pieces}


def to_svg(kind: str, ident: str, overrides: dict | None = None, point_overrides: dict | None = None,
           line_overrides: dict | None = None, compose_choices: dict | None = None) -> str:
    results, _, _ = _evaluate(kind, ident, overrides or {}, point_overrides or {}, line_overrides or {}, compose_choices)
    if kind == "style":
        return render_style_svg(results, labels=False)
    res = results["block"]
    return render_pieces_svg(res) if len(_piece_names(res)) > 1 else render_svg(res)


# ------------------------------------------------------------------ 원본 도면 겹쳐 보기
FITS = ROOT / "verify" / "fits.json"
_fit_lock = threading.Lock()


def _block_page(blk: Block) -> int | None:
    m = re.search(r"p\.(\d+)", str(blk.data.get("source", "")))
    return int(m.group(1)) if m else None


def page_svg_layers(page: int, layers=("pattern", "developed")) -> str:
    """추출한 도면(extracted/**/pNNN.svg)에서 층 그룹의 속만 꺼내 <g> 로 잇는다. 좌표는 PDF pt."""
    files = list(ROOT.glob(f"extracted/*/*/p{page:03d}.svg"))
    if not files:
        raise FileNotFoundError(f"p.{page} 추출 그림이 없다")
    text = files[0].read_text(encoding="utf-8")
    out = []
    for layer in layers:
        m = re.search(rf'<g id="{layer}"[^>]*>(.*?)</g>', text, re.S)
        if m:
            out.append(f'<g class="layer-{layer}">{m.group(1)}</g>')
    return "".join(out)


def overlay_fit(block_id: str, piece: str | None, refresh: bool = False) -> dict:
    """원형(조각)을 원본 도면에 맞춘 변환. 시간이 걸려서 verify/fits.json 에 남긴다."""
    key = f"{block_id}|{piece or ''}"
    with _fit_lock:
        cache = json.loads(FITS.read_text(encoding="utf-8")) if FITS.exists() else {}
    if key in cache and not refresh:
        return cache[key]
    blk = Block.load(ROOT / "blocks" / f"{block_id}.yaml")
    page = _block_page(blk)
    if not page:
        raise ValueError(f"{block_id}: 도면 쪽수(source)가 없다")
    hint = blk.data.get("verify") or {}
    if hint.get("skip"):
        raise ValueError(f"{block_id}: 도면이 없는 원형이다 (verify.skip)")
    sys.path.insert(0, str(ROOT / "tools"))
    import align_block  # noqa: PLC0415
    import contextlib, io  # noqa: PLC0415
    argv = [f"blocks/{block_id}.yaml", "--page", str(page), "--quiet"] + (["--piece", piece] if piece else [])
    with contextlib.redirect_stdout(io.StringIO()):
        sx, sy, ox, oy, err, rot, cx, cy = align_block.main(argv)
    mir = hint.get("mirror")
    fit = {"page": page, "sx": sx, "sy": sy, "ox": ox, "oy": oy, "err": err / sx, "rot": rot, "cx": cx, "cy": cy,
           "mirror": bool(mir is True or (isinstance(mir, list) and piece in mir)),
           "layers": (hint.get("layer") or "pattern,developed")}
    with _fit_lock:
        cache = json.loads(FITS.read_text(encoding="utf-8")) if FITS.exists() else {}
        cache[key] = fit
        FITS.parent.mkdir(exist_ok=True)
        FITS.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return fit


# ------------------------------------------------------------------ 프로젝트 파일 (.pcad)
PROJECTS = ROOT / "projects"


def _safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", (name or "").strip())
    if not name or name.startswith("."):
        raise ValueError("프로젝트 이름이 필요하다")
    return name


def project_list() -> list[dict]:
    out = []
    for f in sorted(PROJECTS.glob("*.pcad")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.append({"name": f.stem, "kind": d.get("kind"), "id": d.get("id"), "saved": d.get("saved", "")})
    return out


def project_save(name: str, data: dict) -> dict:
    import datetime  # noqa: PLC0415
    name = _safe_name(name)
    keep = {k: data.get(k) for k in ("kind", "id", "overrides", "point_overrides", "line_overrides", "piece_settings", "grading", "marker", "compose", "view", "note")}
    keep["name"] = name
    keep["saved"] = datetime.datetime.now().isoformat(timespec="seconds")
    PROJECTS.mkdir(exist_ok=True)
    (PROJECTS / f"{name}.pcad").write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")
    return keep


def project_load(name: str) -> dict:
    f = PROJECTS / f"{_safe_name(name)}.pcad"
    if not f.exists():
        raise FileNotFoundError(f"프로젝트가 없다: {name}")
    return json.loads(f.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ 조각 · 시접 · DXF
def _all_pieces(kind, ident, overrides, point_overrides, line_overrides, piece_settings, compose_choices=None):
    results, _, name = _evaluate(kind, ident, overrides or {}, point_overrides or {}, line_overrides or {}, compose_choices)
    pieces = []
    for key, res in results.items():
        pieces += build_pieces(res, key, piece_settings or {})
    # 재단선 상자로 왼쪽부터 나란히 (펼치면 크기가 달라지므로 그림 배치와 따로 잡는다)
    x = 0.0
    positions = []
    for pc in pieces:
        x0, y0, x1, y1 = pc.bbox()
        positions.append((x - x0, -y0))
        x += (x1 - x0) + 1.5
    return name, pieces, positions


def pieces_json(kind, ident, overrides=None, point_overrides=None, line_overrides=None, piece_settings=None, compose_choices=None) -> dict:
    name, pieces, positions = _all_pieces(kind, ident, overrides, point_overrides, line_overrides, piece_settings, compose_choices)
    from .dxf import _allow_at  # noqa: PLC0415
    out = []
    for pc, (dx, dy) in zip(pieces, positions):
        P = lambda pts: [[p.x, p.y] for p in pts]  # noqa: E731
        out.append({"key": f"{pc.block}.{pc.name}", "block": pc.block, "name": pc.name,
                    "loop": P(pc.loop), "cut": P(pc.cut),
                    "edges": [{"name": e.name, "role": e.role, "allowance": e.allowance, "synthetic": e.synthetic} for e in pc.edges],
                    "notches": [[q.x, q.y, n.x, n.y, _allow_at(pc, q)] for q, n in pc.notches],
                    "grain": P(pc.grain) if pc.grain else None,
                    "internal": [{"name": n, "role": r, "pts": P(pts)} for n, r, pts in pc.internal],
                    "fold": pc.fold, "unfolded": pc.unfolded, "quantity": pc.quantity, "fabric": pc.fabric,
                    "warnings": pc.warnings, "dx": dx, "dy": dy, "bbox": list(pc.bbox())})
    return {"name": name, "pieces": out}


def to_dxf(kind, ident, overrides=None, point_overrides=None, line_overrides=None, piece_settings=None,
           grading=None, compose_choices=None) -> str:
    """grading = {"system", "base", "sizes": [...]} 이면 사이즈마다 조각을 만들어 줄줄이 놓는다 (조각 이름에 호칭을 붙인다)."""
    name, pieces, positions = _all_pieces(kind, ident, overrides, point_overrides, line_overrides, piece_settings, compose_choices)
    if not grading or not grading.get("sizes"):
        return write_dxf(pieces, positions, title=name)
    base = grading["base"]
    all_pieces, all_pos = [], []
    row_h = max(pc.bbox()[3] - pc.bbox()[1] for pc in pieces) + 2.0
    for r, size in enumerate([base] + [s for s in grading["sizes"] if s != base]):
        ov = overrides if size == base else grade_overrides(kind, ident, overrides, grading["system"], base, size, compose_choices)
        _, pcs, pos = _all_pieces(kind, ident, ov, point_overrides, line_overrides, piece_settings, compose_choices)
        for pc, (dx, dy) in zip(pcs, pos):
            pc.name = f"{pc.name}_{size}"
            all_pieces.append(pc)
            all_pos.append((dx, dy + r * row_h))
    return write_dxf(all_pieces, all_pos, title=f"{name} ({grading['system']} {base} 기준)")


# ------------------------------------------------------------------ 그레이딩
def size_systems() -> dict:
    return systems()


def grade_overrides(kind, ident, overrides, system, base, target, compose_choices=None) -> dict:
    """기준 사이즈에서 계산한 치수를 알아야 차이를 더할 수 있다."""
    results, _, _ = _evaluate(kind, ident, overrides or {}, {}, {}, compose_choices)
    base_values = {f"{key}.{n}": v for key, res in results.items() for n, v in res.measurements.items()
                   if isinstance(v, (int, float))}
    blocks = [(k, blk) for k, blk, _ in load_style(ident, compose_choices).blocks] if kind == "style" else None
    return size_overrides(kind, ident, overrides or {}, base_values, system, base, target, blocks)


def grade_json(kind, ident, overrides, point_overrides, line_overrides, system, base, sizes, compose_choices=None) -> dict:
    """사이즈마다 선(완성선·골선·다트)만 — 겹쳐 보기용. 조각 자리는 기준 사이즈 배치를 그대로 쓴다."""
    base_results, _, _ = _evaluate(kind, ident, overrides or {}, point_overrides or {}, line_overrides or {}, compose_choices)

    def centers(results):
        out = {}
        for key, res in results.items():
            for pc in _piece_names(res):
                x0, y0, x1, y1 = _bbox([l for l in res.lines if (l.piece or "") == pc])
                out[(key, pc)] = ((x0 + x1) / 2, (y0 + y1) / 2)
        return out
    base_c = centers(base_results)
    out = []
    for size in sizes:
        if size == base:
            continue
        ov = grade_overrides(kind, ident, overrides, system, base, size, compose_choices)
        results, _, _ = _evaluate(kind, ident, ov, point_overrides or {}, line_overrides or {}, compose_choices)
        size_c = centers(results)
        blocks = []
        for key, res in results.items():
            # 조각마다 기준 사이즈 조각의 가운데에 포개 놓는다 — 원형 좌표에서는 큰 사이즈의 뒤판이 오른쪽으로 밀리므로
            shift = {pc: [base_c[(key, pc)][0] - size_c[(key, pc)][0], base_c[(key, pc)][1] - size_c[(key, pc)][1]]
                     for (k, pc) in size_c if k == key and (key, pc) in base_c}
            lines = [{"name": l.name, "role": l.role, "kind": l.kind, "piece": l.piece or "",
                      "pts": [[p.x, p.y] for p in l.pts],
                      "beziers": [[[b.p0.x, b.p0.y], [b.c1.x, b.c1.y], [b.c2.x, b.c2.y], [b.p3.x, b.p3.y]] for b in l.beziers]}
                     for l in res.lines if l.role in ("outline", "fold", "dart")]
            changed = {n: round(v, 4) for n, v in res.measurements.items()
                       if isinstance(v, (int, float)) and f"{key}.{n}" in ov and ov[f"{key}.{n}"] != (overrides or {}).get(f"{key}.{n}")}
            blocks.append({"key": key, "lines": lines, "changed": changed, "shift": shift})
        out.append({"size": size, "blocks": blocks})
    return {"system": system, "base": base, "sizes": out}


# ------------------------------------------------------------------ 마카
def marker_dxf(kind, ident, overrides, point_overrides, line_overrides, piece_settings, placements, width=58.0,
               grading=None, compose_choices=None, fmt="dxf") -> str:
    """placements: [{"key": "body.앞판", "size": "55"(선택), "rot": 90, "flip": false, "x": 3.2, "y": 0.5}] — 원단 좌표(인치).
    놓인 조각을 실제 좌표로 바꿔 한 층에 쓴다 (조각 이름에 번호)."""
    cache = {}

    def pieces_for(size):
        if size not in cache:
            ov = overrides
            if grading and size and size != grading.get("base"):
                ov = grade_overrides(kind, ident, overrides, grading["system"], grading["base"], size, compose_choices)
            _, pcs, _ = _all_pieces(kind, ident, ov, point_overrides, line_overrides, piece_settings, compose_choices)
            cache[size] = {f"{pc.block}.{pc.name}": pc for pc in pcs}
        return cache[size]
    placed = []
    for i, pl in enumerate(placements or []):
        pcs = pieces_for(pl.get("size") or (grading or {}).get("base"))
        pc = pcs.get(pl["key"])
        if not pc:
            continue
        t = transform_piece(pc, float(pl.get("rot", 0)), bool(pl.get("flip")), float(pl.get("x", 0)), float(pl.get("y", 0)))
        t.name = f"{pc.name}{'_' + pl['size'] if pl.get('size') else ''}_{i + 1}"
        placed.append(t)
    title = f"marker {ident} width {width}in"
    if fmt == "hpgl":
        return write_hpgl(placed, [(0.0, 0.0)] * len(placed))
    return write_dxf(placed, [(0.0, 0.0)] * len(placed), title=title)


def to_hpgl(kind, ident, overrides=None, point_overrides=None, line_overrides=None, piece_settings=None, compose_choices=None) -> str:
    _, pieces, positions = _all_pieces(kind, ident, overrides, point_overrides, line_overrides, piece_settings, compose_choices)
    return write_hpgl(pieces, positions)


# ------------------------------------------------------------------ 디테일 옵션
def options_json(style_id: str, compose_choices: dict | None = None) -> dict:
    st = load_style(style_id, compose_choices)
    return {"style": style_id, "category": category_of(style_id), "slots": availability(st, category_of(style_id))}
