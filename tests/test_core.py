"""python -m pytest tests  또는  python -m unittest discover tests"""

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from patterncad.block import Block  # noqa: E402
from patterncad.expr import Env, evaluate  # noqa: E402
from patterncad.geometry import Bezier, Pt, circle_line_x, fit_handles, intersect_lines  # noqa: E402
from patterncad.units import parse_inch, to_fraction  # noqa: E402


class Units(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_inch("3.1/2"), 3.5)  # 포트폴리오식
        self.assertEqual(parse_inch("3 1/2"), 3.5)
        self.assertEqual(parse_inch("3-1/2"), 3.5)
        self.assertEqual(parse_inch("1/8”"), 0.125)
        self.assertEqual(parse_inch("26”"), 26.0)
        self.assertEqual(parse_inch("3.5"), 3.5)
        self.assertEqual(parse_inch(" 14.5/8\" "), 14.625)
        self.assertIsNone(parse_inch("B/4"))

    def test_format(self):
        self.assertEqual(to_fraction(3.5), "3 1/2")
        self.assertEqual(to_fraction(3.5, style="dot"), "3.1/2")
        self.assertEqual(to_fraction(0.125), "1/8")
        self.assertEqual(to_fraction(14.625, 8), "14 5/8")


class Geometry(unittest.TestCase):
    def test_intersect(self):
        p = intersect_lines(Pt(0, 0), Pt(2, 2), Pt(0, 2), Pt(2, 0))
        self.assertAlmostEqual(p.x, 1)
        self.assertAlmostEqual(p.y, 1)

    def test_circle_x(self):
        p = circle_line_x(Pt(0, 0), 5, 3)
        self.assertAlmostEqual(p.y, 4)

    def test_bezier_length(self):
        b = Bezier(Pt(0, 0), Pt(1, 0), Pt(2, 0), Pt(3, 0))
        self.assertAlmostEqual(b.length(), 3, places=6)

    def test_fit_recovers_handles(self):
        p0, p3 = Pt(0, 0), Pt(10, 0)
        t0, t3 = Pt(1, 1), Pt(1, -1)
        from patterncad.geometry import bezier_from_tangents

        truth = bezier_from_tangents(p0, t0, p3, t3, 0.4, 0.3)
        h0, h3 = fit_handles(p0, t0, p3, t3, truth.sample(40))
        self.assertAlmostEqual(h0, 0.4, places=2)
        self.assertAlmostEqual(h3, 0.3, places=2)


class Expr(unittest.TestCase):
    def test_eval(self):
        env = Env({"B": 33.5, "여유": 2.5}, {"A": Pt(1, 2), "C": Pt(4, 6)})
        self.assertAlmostEqual(evaluate("B/4 + 여유/4", env), 9)
        self.assertAlmostEqual(evaluate("dist(A, C)", env), 5)
        self.assertAlmostEqual(evaluate("C.y - A.y", env), 4)
        self.assertAlmostEqual(evaluate("3.1/2", env), 3.5)
        with self.assertRaises(ValueError):
            evaluate("__import__('os')", env)


class Sichuni(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = Block.load(ROOT / "blocks" / "sichuni_basic.yaml").evaluate()

    def test_frame(self):
        p = self.res.points
        self.assertAlmostEqual(p["TOP_CB"].x, 18)  # B/2 + 여유/2
        self.assertAlmostEqual(p["UP"].x, 9)
        self.assertAlmostEqual(p["UP"].y, 8.5)
        self.assertAlmostEqual(p["HEM_CF"].y, 16.25)

    def test_front_shoulder_is_back_minus_dart(self):
        r = self.res
        back = r.line("뒤어깨선목쪽").length() + r.line("뒤어깨선끝쪽").length()
        self.assertAlmostEqual(r.line("앞어깨선").length(), back, places=6)

    def test_dart_width(self):
        p = self.res.points
        self.assertAlmostEqual(p["BSD1"].dist(p["BSD2"]), 0.25)
        self.assertAlmostEqual(p["BD1"].dist(p["BD2"]), 1.25)

    def test_armholes_meet_at_underarm(self):
        r = self.res
        self.assertEqual(r.line("앞암홀").pts[-1], r.points["UP"])
        self.assertEqual(r.line("뒤암홀").pts[-1], r.points["UP"])

    def test_resize_moves_points(self):
        big = Block.load(ROOT / "blocks" / "sichuni_basic.yaml").evaluate({"B": 37.5})
        self.assertAlmostEqual(big.points["UP"].x, 10)
        self.assertAlmostEqual(big.points["TOP_CB"].x, 20)


if __name__ == "__main__":
    unittest.main()
