"""디테일 옵션 — 스타일의 부속(칼라·소매·커프스·밑단·오비)을 갈아끼운다. 재료는 data/options.yaml."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .block import Block
from .style import Style
from .units import parse_inch

ROOT = Path(__file__).resolve().parent.parent
BASE_KEYS = ("body", "skirt", "pants")


def options() -> list[dict]:
    with open(ROOT / "data" / "options.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["slots"]


def _block_ids(choice: dict) -> set[str]:
    return {b["block"] for b in (choice.get("blocks") or {}).values()}


def _pairs(choice: dict) -> set[tuple]:
    """(키, 원형 id) 쌍 — rib 처럼 여러 슬롯이 같은 원형을 쓰므로 키까지 봐야 한다."""
    return {(k, b["block"]) for k, b in (choice.get("blocks") or {}).items()}


def base_key(st: Style) -> str:
    return next((k for k, _, _ in st.blocks if k in BASE_KEYS), st.blocks[0][0])


def current_choices(st: Style) -> dict:
    """스타일에 지금 들어 있는 부속으로 슬롯별 선택지를 알아낸다 (모르면 none)."""
    have = {(k, blk.id) for k, blk, _ in st.blocks}
    out = {}
    for slot in options():
        pick, best = "none", 0
        for ch in slot["choices"]:
            pairs = _pairs(ch)
            if pairs and pairs <= have and len(pairs) > best:   # 밴드+칼라가 다 있으면 '밴드만' 보다 '셔츠 칼라'
                pick, best = ch["id"], len(pairs)
        out[slot["id"]] = pick
    return out


def availability(st: Style, category: str | None) -> list[dict]:
    """슬롯·선택지 목록 + 이 스타일에서 가능한지 (requires 검사)."""
    base = base_key(st)
    res = st.evaluate()
    b = res[base]
    meas, lines = set(b.measurements), {l.name for l in b.lines}
    cur = current_choices(st)
    out = []
    for slot in options():
        if category and slot.get("applies") and category not in slot["applies"]:
            continue
        chs = []
        for ch in slot["choices"]:
            missing = [r for r in (ch.get("requires") or []) if (r[5:] not in lines if r.startswith("line:") else r not in meas)]
            chs.append({"id": ch["id"], "label": ch["label"], "available": not missing, "missing": missing,
                        "blocks": sorted(_block_ids(ch))})
        out.append({"id": slot["id"], "label": slot["label"], "current": cur.get(slot["id"], "none"),
                    "needs_slot": slot.get("needs_slot"), "choices": chs})
    return out


def compose(st: Style, choices: dict | None) -> Style:
    """선택한 슬롯의 부속을 갈아끼운 새 Style. choices = {"collar": "stand", "sleeve": "two_piece"}."""
    if not choices:
        return st
    base = base_key(st)
    slots = {s["id"]: s for s in options()}
    blocks = list(st.blocks)
    added = []
    for sid, cid in choices.items():
        slot = slots.get(sid)
        if not slot or cid is None:
            continue
        all_pairs = set().union(*(_pairs(c) for c in slot["choices"]))
        removed = [(k, blk, m) for k, blk, m in blocks if (k, blk.id) in all_pairs]
        blocks = [(k, blk, m) for k, blk, m in blocks if (k, blk.id) not in all_pairs]
        ch = next((c for c in slot["choices"] if c["id"] == cid), None)
        if not ch or not ch.get("blocks"):
            continue
        inherit = {}
        for _, _, m in removed:   # 다른 원형을 참조하는 연결식은 이어받지 않는다 (빠진 원형을 가리킬 수 있다)
            inherit.update({k: v for k, v in m.items()
                            if not (isinstance(v, str) and parse_inch(v) is None and any(c in v for c in "(."))})
        for key, spec in ch["blocks"].items():
            blk = Block.load(ROOT / "blocks" / f"{spec['block']}.yaml")
            meas = {k: (v.replace("{base}", base) if isinstance(v, str) else v) for k, v in (spec.get("measurements") or {}).items()}
            for k, v in inherit.items():           # 빠진 부속의 치수 중 새 원형에 있는 것은 이어받는다 (연결식은 새것 우선)
                if k in blk.measurements and k not in meas:
                    meas[k] = v
            added.append((key, blk, meas))
    # 순서: 몸판 → 소매 → 나머지 (커프스 시보리가 소매를 참조한다)
    ordered = [b for b in blocks if b[0] == base] + [b for b in blocks + added if b[0] == "sleeve"] + \
              [b for b in blocks if b[0] not in (base, "sleeve")] + [b for b in added if b[0] != "sleeve"]
    seen, final = set(), []
    for b in ordered:
        if b[0] in seen:
            continue
        seen.add(b[0])
        final.append(b)
    label = ", ".join(f"{k}={v}" for k, v in choices.items() if v)
    return Style(f"{st.name} ({label})" if label else st.name, st.id, final, st.source, dict(st.data))


def category_of(style_id: str) -> str | None:
    from .wizard import catalog  # noqa: PLC0415
    for c in catalog()["categories"]:
        if any(i["style"] == style_id for i in c["items"]):
            return c["id"]
    return None
