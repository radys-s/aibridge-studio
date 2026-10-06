import unittest

from calc.stats import mean


class MeanTest(unittest.TestCase):
    def test_mean(self) -> None:
        self.assertEqual(mean([1, 2, 3]), 2)

    def test_empty(self) -> None:
        with self.assertRaises(ValueError):
            mean([])


if __name__ == "__main__":
    unittest.main()
