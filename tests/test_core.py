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

    def test_front_shoulder_points_at_guide(self):
        p = self.res.points
        d1 = (p["SP_F"] - p["SNP_F"]).unit()
        d2 = (p["G_SF"] - p["SNP_F"]).unit()
        self.assertAlmostEqual(d1.cross(d2), 0, places=9)
        self.assertAlmostEqual(p["G_SF"].x, 7.5)
        self.assertAlmostEqual(p["G_SF"].y, 2)

    def test_scapula_dart_perpendicular_to_shoulder(self):
        p = self.res.points
        shoulder = (p["SP_B"] - p["SNP_B"]).unit()
        leg = p["SCAP"] - p["BSD_M"]
        self.assertAlmostEqual(shoulder.dot(leg), 0, places=9)
        self.assertAlmostEqual(leg.length(), 4)

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


class SichuniDartless(unittest.TestCase):
    """기본 원형을 상속한 무다트 원형."""

    @classmethod
    def setUpClass(cls):
        cls.res = Block.load(ROOT / "blocks" / "sichuni_dartless.yaml").evaluate()

    def test_inherits_and_overrides(self):
        r = self.res
        self.assertEqual(r.block.data["extends_from"], "sichuni_basic")
        self.assertAlmostEqual(r.points["UP"].x, 9.25)  # 여유 3.1/2
        self.assertAlmostEqual(r.points["UP"].y, 8.625)
        self.assertAlmostEqual(r.points["HEM_CF"].y, r.points["WL_CB"].y)  # 앞처짐 0 → 밑단 같은 높이
        self.assertAlmostEqual(r.points["SNP_F"].y, -1.25)  # 앞올림

    def test_no_darts(self):
        names = {l.name for l in self.res.lines}
        self.assertNotIn("가슴다트", names)
        self.assertNotIn("뒤어깨다트", names)
        self.assertIn("앞옆선", names)
        self.assertIn("뒤어깨선", names)
        self.assertNotIn("BP", self.res.points)

    def test_front_shoulder_is_back_minus_ease(self):
        r = self.res
        self.assertAlmostEqual(r.line("앞어깨선").length(), r.line("뒤어깨선").length() - 0.25, places=6)


class Sleeve(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = Block.load(ROOT / "blocks" / "sleeve_basic.yaml").evaluate()

    def test_cap_height_formula(self):
        m = self.res.measurements
        self.assertAlmostEqual(m["소매산높이"], (m["앞AH"] + m["뒤AH"]) / 3 + m["소매산조정"])

    def test_slant_lengths(self):
        p, m = self.res.points, self.res.measurements
        self.assertAlmostEqual(p["SCP"].dist(p["BIC_B"]), m["뒤AH"] + m["뒤소매이세"])
        self.assertAlmostEqual(p["SCP"].dist(p["BIC_F"]), m["앞AH"] + m["앞소매이세"])

    def test_crossing_on_slant(self):
        p = self.res.points
        d = (p["BIC_B"] - p["SCP"]).unit()
        self.assertAlmostEqual((p["X_B"] - p["SCP"]).cross(d), 0, places=9)

    def test_elbow_on_elbow_line(self):
        p, m = self.res.points, self.res.measurements
        self.assertAlmostEqual(p["EL_B"].y, m["팔꿈치길이"])
        self.assertAlmostEqual(p["EL_F"].y, m["팔꿈치길이"])

    def test_cap_level_sets_adjust_and_ease(self):
        b = Block.load(ROOT / "blocks" / "sleeve_basic.yaml")
        low = b.evaluate({"소매산단계": "낮음"}).measurements
        self.assertAlmostEqual(low["소매산조정"], -1.625)
        self.assertAlmostEqual(low["앞소매이세"], -0.5)
        # 이세는 단계와 별개로 덮어쓸 수 있다
        custom = b.evaluate({"소매산단계": "낮음", "앞소매이세": "1/4"}).measurements
        self.assertAlmostEqual(custom["소매산조정"], -1.625)
        self.assertAlmostEqual(custom["앞소매이세"], 0.25)
        with self.assertRaises(ValueError):
            b.evaluate({"소매산단계": "없는단계"})

    def test_lower_cap_lowers_height(self):
        low = Block.load(ROOT / "blocks" / "sleeve_basic.yaml").evaluate({"소매산조정": -1.625})
        self.assertLess(low.measurements["소매산높이"], self.res.measurements["소매산높이"])
        self.assertGreater(low.points["BIC_F"].x - low.points["BIC_B"].x,
                           self.res.points["BIC_F"].x - self.res.points["BIC_B"].x)  # 낮은 소매산 → 소매통 넓어짐


class Blouse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = Block.load(ROOT / "blocks" / "shirt_collar_blouse_body.yaml").evaluate()

    def test_overrides_from_block(self):
        p, m = self.res.points, self.res.measurements
        self.assertAlmostEqual(m["진동깊이"], 8.25)
        self.assertAlmostEqual(p["UP2"].x, 9.25)  # 옆선여유 1/4
        self.assertAlmostEqual(p["UP2"].y, 8.375)  # 진동내림 1/8
        self.assertAlmostEqual(p["HEM_CF"].y, 24.25)  # 기장 + 앞내림
        self.assertAlmostEqual(p["CB_WL"].x, 17.25)  # 뒤중심 허리 3/4 들임

    def test_front_shoulder_equals_back(self):
        r = self.res
        self.assertAlmostEqual(r.line("앞어깨선").length(), r.line("뒤어깨선").length(), places=6)

    def test_waist_darts(self):
        p = self.res.points
        self.assertAlmostEqual(p["FWD_L"].x, 5.375)
        self.assertAlmostEqual(p["FWD_R"].x - p["FWD_L"].x, 1)
        self.assertAlmostEqual(p["CB_WL"].x - p["BWD_R"].x, 4.25)

    def test_style_sleeve_follows_blouse_armhole(self):
        from patterncad.style import Style

        res = Style.load(ROOT / "styles" / "shirt_collar_blouse.yaml").evaluate()
        s, b = res["sleeve"], res["body"]
        self.assertAlmostEqual(s.measurements["AH"], b.line("앞암홀").length() + b.line("뒤암홀").length())
        self.assertAlmostEqual(s.measurements["소매산조정"], -0.5)


class StyleLink(unittest.TestCase):
    """몸판 암홀 길이가 소매로 자동 전달되는 스타일."""

    def test_armhole_length_flows_to_sleeve(self):
        from patterncad.style import Style

        st = Style.load(ROOT / "styles" / "sichuni_with_sleeve.yaml")
        res = st.evaluate()
        self.assertAlmostEqual(res["sleeve"].measurements["앞AH"], res["body"].line("앞암홀").length())
        self.assertAlmostEqual(res["sleeve"].measurements["뒤AH"], res["body"].line("뒤암홀").length())
        big = st.evaluate({"body.B": 37.5})
        self.assertGreater(big["sleeve"].measurements["앞AH"], res["sleeve"].measurements["앞AH"])


if __name__ == "__main__":
    unittest.main()
