"""스타일: 원형 여러 개를 묶고 치수를 서로 연결한다.

    name: 시추니 원형 + 소매
    blocks:                                   # 순서대로 계산한다
      body:   {block: ../blocks/sichuni_basic.yaml}
      sleeve: {block: ../blocks/sleeve_basic.yaml,
               measurements: {앞AH: "len(body.앞암홀)", 뒤AH: "len(body.뒤암홀)", 소매산단계: 기본, 앞소매이세: -1/4}}

치수 덮어쓰기 값으로 쓸 수 있는 것:
    숫자 / 인치표기 / 선택값 글자        그대로
    len(블록.선이름)                       앞서 계산한 블록의 선 길이 (곡선이면 곡선 길이)
    블록.치수이름                          앞서 계산한 블록의 치수
    블록.점이름.x / .y                     앞서 계산한 블록의 점 좌표
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .block import Block, Resolved
from .expr import Env, evaluate
from .units import parse_inch

_LEN = re.compile(r"len\(\s*(\w+)\.([^)\s]+)\s*\)")
_REF = re.compile(r"\b(\w+)\.([^.\s()+\-*/,]+)(?:\.(x|y))?")


@dataclass
class Style:
    name: str
    id: str
    blocks: list  # [(이름, Block, overrides dict)]
    source: Path | None = None
    data: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path) -> "Style":
        path = Path(path)
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        blocks = []
        for name, spec in data["blocks"].items():
            blk = Block.load(path.parent / spec["block"])
            blocks.append((name, blk, dict(spec.get("measurements") or {})))
        return cls(data.get("name", ""), data.get("id", path.stem), blocks, path, data)

    def evaluate(self, overrides: dict | None = None) -> dict[str, Resolved]:
        """{블록이름: Resolved}. overrides 는 {"sleeve.소매산단계": "낮음"} 꼴로 블록별 치수를 덮어쓴다."""
        overrides = overrides or {}
        done: dict[str, Resolved] = {}
        for name, blk, meas in self.blocks:
            ov = {}
            for k, v in meas.items():
                ov[k] = self._resolve(v, done)
            for k, v in overrides.items():
                if k.startswith(name + "."):
                    ov[k[len(name) + 1:]] = self._resolve(v, done)
            done[name] = blk.evaluate(ov)
        return done

    @staticmethod
    def _resolve(v, done: dict[str, Resolved]):
        """앞선 블록을 참조하는 식을 값으로. len(블록.선), 블록.치수, 블록.점.x 를 숫자로 바꾼 뒤 계산한다."""
        if not isinstance(v, str):
            return v
        s = v.strip()
        n = parse_inch(s)
        if n is not None:
            return n

        def lookup(blk, key, comp=None):
            res = done[blk]
            if key in res.points:
                return getattr(res.points[key], comp or "x")
            if key in res.measurements:
                return res.measurements[key]
            raise KeyError(f"{blk} 에 {key} 가 없다")

        def sub_len(m):
            return repr(done[m.group(1)].line(m.group(2)).length())

        def sub_ref(m):
            if m.group(1) not in done:
                return m.group(0)
            return repr(lookup(m.group(1), m.group(2), m.group(3)))

        t = _REF.sub(sub_ref, _LEN.sub(sub_len, s))
        if t == s and not any(c in s for c in "+-*/0123456789"):
            return s  # 선택값 글자 (예: 기본)
        try:
            return evaluate(t, Env({}, {}))
        except (SyntaxError, ValueError, NameError) as e:
            raise ValueError(f"스타일 치수 식을 계산 못 함: {v!r} → {t!r} ({e})") from e


def bbox(res: Resolved):
    """선 위의 점만으로 범위를 잡는다 (부채꼴 중심 같은 먼 보조점 제외)."""
    xs, ys = [], []
    for l in res.lines:
        for p in l.polyline(8):
            xs.append(p.x)
            ys.append(p.y)
    if not xs:
        xs = [p.x for p in res.points.values()]
        ys = [p.y for p in res.points.values()]
    return min(xs), min(ys), max(xs), max(ys)
