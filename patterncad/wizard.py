"""새 패턴 마법사의 재료 — 카테고리 → 아이템(스타일) → 신체 치수 → 핏.

포트폴리오 추출(extracted/index.json)의 아이템 목록과 사이즈표를 스타일에 잇는다.
스타일은 source 쪽수로 아이템을 찾는다. 사이즈표의 신체 치수는 원형 치수의 ko 이름(가슴둘레 …)으로
원형 치수에 대입된다. 핏 단계는 data/fit_levels.yaml 의 여유 가감을 더한다 (초안).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from .block import Block
from .style import Style
from .units import parse_inch

ROOT = Path(__file__).resolve().parent.parent
CATEGORY_KO = {"top": "상의", "skirt": "스커트", "pants": "팬츠", "jacket": "아우터 · 자켓"}
CATEGORY_ORDER = ["top", "skirt", "pants", "jacket"]  # 개발 순서와 같다


def _index() -> list[dict]:
    with open(ROOT / "extracted" / "index.json", encoding="utf-8") as f:
        return json.load(f)


def _page_of(source: str) -> int | None:
    m = re.search(r"p\.(\d+)", source or "")
    return int(m.group(1)) if m else None


def fit_levels() -> dict:
    with open(ROOT / "data" / "fit_levels.yaml", encoding="utf-8") as f:
        d = yaml.safe_load(f)
    out = {}
    for level, table in d["levels"].items():
        out[level] = {k: parse_inch(v) if not isinstance(v, (int, float)) else float(v) for k, v in table.items()}
    return out


def catalog() -> dict:
    """{"categories": [{id, ko, items: [{style, name, item, pages, size: [{name, body, pattern}]}]}], "fit_levels": …}"""
    items = _index()
    by_page = {}
    for it in items:
        for pg in it["pages"]:
            by_page[pg] = it
    cats = {c: [] for c in CATEGORY_ORDER}
    for f in sorted((ROOT / "styles").glob("*.yaml")):
        with open(f, encoding="utf-8") as fh:
            d = yaml.safe_load(fh) or {}
        pg = _page_of(d.get("source", ""))
        it = by_page.get(pg)
        if not it:
            continue
        size = [{"name": m["name"], "body": m["body_in"], "pattern": m["pattern_in"]}
                for m in it["size"]["measurements"]]
        cats.setdefault(it["category"], []).append({
            "style": d.get("id", f.stem), "name": d.get("name", f.stem), "item": it["title_ko"],
            "slug": it["slug"], "pages": it["pages"], "size": size})
    return {"categories": [{"id": c, "ko": CATEGORY_KO.get(c, c), "items": cats[c]} for c in CATEGORY_ORDER if cats.get(c)],
            "fit_levels": fit_levels()}


def _ko_key(ko: str) -> str:
    return re.sub(r"\(.*?\)", "", ko or "").strip()


def overrides_for(style_id: str, body: dict, fit: str | None) -> dict:
    """마법사 선택 → 치수 덮어쓰기 {"body.B": 34, …}.

    body 는 {"가슴둘레": 34, …} (인치). 원형 치수의 ko 가 같은 이름이면 대입한다.
    핏은 fit_levels 의 가감을 원형의 여유 치수에 더한다 (value 인 치수에만 — 식으로 정해진 여유는 건드리지 않는다)."""
    st = Style.load(ROOT / "styles" / f"{style_id}.yaml")
    levels = fit_levels()
    delta = levels.get(fit or "", {})
    out = {}
    for i, (key, blk, style_meas) in enumerate(st.blocks):
        for name, spec in blk.measurements.items():
            spec = spec if isinstance(spec, dict) else {"value": spec}
            if "value" not in spec:
                continue
            ko = _ko_key(spec.get("ko", ""))
            if ko in body and body[ko] is not None:
                out[f"{key}.{name}"] = float(body[ko])
            elif i == 0 and name in delta and delta[name]:  # 핏 가감은 몸판(첫 원형)에만 — 커프스 여유 같은 부속은 그대로
                base = style_meas.get(name, spec["value"])
                base_v = parse_inch(base) if isinstance(base, str) else float(base)
                if base_v is not None:
                    out[f"{key}.{name}"] = base_v + delta[name]
    return out
