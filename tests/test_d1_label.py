from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, patch
import unittest

from spamdet.d1_label import main, parse_sample_ids


class D1LabelCLITests(unittest.TestCase):
    def test_parse_ids_accepts_batch_and_deduplicates(self) -> None:
        self.assertEqual(parse_sample_ids("1, 2,2,3"), [1, 2, 3])

    def test_parse_ids_rejects_invalid_values(self) -> None:
        for value in ("", "1,", "0", "-1", "1 two"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                parse_sample_ids(value)

    def test_main_dispatches_label_and_ids(self) -> None:
        operation = AsyncMock(return_value=[1, 2, 3])
        with patch("spamdet.d1_label.label_samples_from_env", operation):
            self.assertEqual(main(["--label", "ham", "--ids", "1,2,3"]), 0)
        operation.assert_awaited_once_with("ham", [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
