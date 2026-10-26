#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_zt_resmp


class DispatcherTests(unittest.TestCase):
    def test_report_is_under_ignored_build(self) -> None:
        self.assertEqual(
            check_zt_resmp.REPORT,
            check_zt_resmp.ROOT / "build/reports/public-check",
        )
        self.assertIsInstance(check_zt_resmp.ROOT, Path)


if __name__ == "__main__":
    unittest.main()
