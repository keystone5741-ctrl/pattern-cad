"""치수 식 평가. 'B/4 + 여유/4', 'dist(SNP_B, SP_B) - 뒤어깨다트폭', 'SP_F.y' 같은 식을 안전하게 계산한다."""

from __future__ import annotations

import ast
import math
import operator

from .units import parse_inch

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


class Env:
    """이름 → 값. 치수는 실수, 점은 Pt. 'P.x' 형태로 점 좌표를 읽는다."""

    def __init__(self, measurements: dict, points: dict):
        self.m = measurements
        self.p = points

    def value(self, name: str):
        if name in self.m:
            return self.m[name]
        if name in self.p:
            return self.p[name]
        raise NameError(f"모르는 이름: {name}")


def _funcs(env: Env):
    def dist(a, b):
        return a.dist(b)

    return {"dist": dist, "sqrt": math.sqrt, "min": min, "max": max, "abs": abs}


def evaluate(expr, env: Env):
    """숫자/인치표기 문자열/식 문자열을 값으로."""
    if isinstance(expr, (int, float)):
        return float(expr)
    s = str(expr).strip()
    v = parse_inch(s)
    if v is not None:
        return v
    tree = ast.parse(s, mode="eval")
    return _eval(tree.body, env, _funcs(env))


def _eval(node, env: Env, funcs):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name):
        return env.value(node.id)
    if isinstance(node, ast.Attribute):
        base = _eval(node.value, env, funcs)
        if node.attr in ("x", "y"):
            return getattr(base, node.attr)
        raise ValueError(f"점에서 읽을 수 있는 건 x, y 뿐: .{node.attr}")
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left, env, funcs), _eval(node.right, env, funcs))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand, env, funcs))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in funcs:
        return funcs[node.func.id](*[_eval(a, env, funcs) for a in node.args])
    raise ValueError(f"허용되지 않는 식: {ast.dump(node)}")
