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
from .units import parse_inch

_LEN = re.compile(r"^len\(\s*(\w+)\.(\S+?)\s*\)$")
_REF = re.compile(r"^(\w+)\.([^.\s]+)(?:\.(x|y))?$")


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
        if not isinstance(v, str):
            return v
        s = v.strip()
        if parse_inch(s) is not None:
            return parse_inch(s)
        m = _LEN.match(s)
        if m:
            blk, line = m.group(1), m.group(2)
            return done[blk].line(line).length()
        m = _REF.match(s)
        if m and m.group(1) in done:
            res, key, comp = done[m.group(1)], m.group(2), m.group(3)
            if key in res.points:
                return getattr(res.points[key], comp or "x")
            if key in res.measurements:
                return res.measurements[key]
            raise KeyError(f"{m.group(1)} 에 {key} 가 없다")
        return s  # 선택값 글자 등


def bbox(res: Resolved):
    xs, ys = [], []
    for l in res.lines:
        for p in l.polyline(8):
            xs.append(p.x)
            ys.append(p.y)
    for p in res.points.values():
        xs.append(p.x)
        ys.append(p.y)
    return min(xs), min(ys), max(xs), max(ys)
