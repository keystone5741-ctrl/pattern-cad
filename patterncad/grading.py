"""그레이딩 — 치수 재대입 방식.

사이즈 체계(data/sizes.yaml)의 호칭별 신체 치수를 원형 치수(ko 이름이 같은 것)에 넣어 원형을 다시 그린다.
기준 사이즈에서 다른 사이즈로 갈 때는 **차이만** 더한다 — 사용자가 기준 사이즈에서 손본 치수·점·핸들
수정값이 그대로 살아 있게. (점별 편차 방식은 나중에.)
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .block import Block
from .style import Style
from .units import parse_inch

ROOT = Path(__file__).resolve().parent.parent


def _num(v):
    return float(v) if isinstance(v, (int, float)) else parse_inch(v)


def systems() -> dict:
    with open(ROOT / "data" / "sizes.yaml", encoding="utf-8") as f:
        d = yaml.safe_load(f)
    out = {}
    for name, sy in d["systems"].items():
        sizes = [str(s) for s in sy["sizes"]]
        meas = {k: [_num(v) for v in vals] for k, vals in sy["measurements"].items()}
        for k, vals in meas.items():
            if len(vals) != len(sizes):
                raise ValueError(f"사이즈 체계 {name}: {k} 값이 {len(vals)}개, 호칭은 {len(sizes)}개")
        out[name] = {"sizes": sizes, "aliases": {str(a): str(b) for a, b in (sy.get("aliases") or {}).items()},
                     "measurements": meas}
    return out


def _ko_key(ko: str) -> str:
    return re.sub(r"\(.*?\)", "", ko or "").strip()


def _blocks(kind: str, ident: str):
    if kind == "style":
        st = Style.load(ROOT / "styles" / f"{ident}.yaml")
        return [(key, blk) for key, blk, _ in st.blocks]
    return [("block", Block.load(ROOT / "blocks" / f"{ident}.yaml"))]


def size_overrides(kind: str, ident: str, base_overrides: dict, base_values: dict,
                   system: str, base: str, target: str, blocks=None) -> dict:
    """기준 사이즈의 치수 덮어쓰기 → 목표 사이즈의 덮어쓰기.

    base_values 는 기준 사이즈로 계산한 결과의 {"block.치수": 값} (덮어쓰기가 없어도 원형 기본값을 알아야 차이를 더한다)."""
    sy = systems()[system]
    idx = {s: i for i, s in enumerate(sy["sizes"])}
    b, t = sy["aliases"].get(base, base), sy["aliases"].get(target, target)
    if b not in idx or t not in idx:
        raise ValueError(f"{system} 에 없는 호칭: {base} / {target}")
    out = dict(base_overrides)
    for key, blk in (blocks if blocks is not None else _blocks(kind, ident)):
        for name, spec in blk.measurements.items():
            spec = spec if isinstance(spec, dict) else {"value": spec}
            if "value" not in spec:
                continue
            ko = _ko_key(spec.get("ko", "")) or name
            row = sy["measurements"].get(ko) or sy["measurements"].get(name)
            if not row:
                continue
            delta = row[idx[t]] - row[idx[b]]
            if abs(delta) < 1e-9:
                continue
            k = f"{key}.{name}"
            cur = base_overrides.get(k, base_values.get(k))
            if cur is None:
                continue
            cur = _num(cur) if isinstance(cur, str) else float(cur)
            out[k] = cur + delta
    return out
