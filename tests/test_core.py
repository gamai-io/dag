import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reflex


class TestReflex(unittest.TestCase):
    def test_risk_blocks_catastrophic(self):
        self.assertGreaterEqual(reflex.risk_score("rm -rf /"), 0.9)
        self.assertGreaterEqual(reflex.risk_score("drop table users"), 0.9)

    def test_risk_allows_normal(self):
        self.assertLess(reflex.risk_score("koliko kupaca ima"), 0.3)
        self.assertLess(reflex.risk_score("napisi python funkciju"), 0.3)

    def test_domain(self):
        self.assertEqual(reflex.detect_domain("select * from table"), "sql")
        self.assertEqual(reflex.detect_domain("napisi python funkciju"), "code")

    def test_translit(self):
        self.assertEqual(reflex.translit("čćžšđ"), "cczsd")


if __name__ == "__main__":
    unittest.main()
