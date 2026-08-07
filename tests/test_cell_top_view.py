from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.engine import CognitiveEngine


class CellTopViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "mai_test.db"
        self.engine = CognitiveEngine(self.db_path)

    def tearDown(self) -> None:
        self.engine.close()
        self.tmp.cleanup()

    def test_repeated_path_becomes_unit(self) -> None:
        first = self.engine.process_sentence("눈꽃")
        self.assertEqual(first["word_segments"], ["눈", "꽃"])

        second = self.engine.process_sentence("눈꽃")
        self.assertEqual(second["word_segments"], ["눈꽃"])
        self.assertEqual(second["pass_results"][0]["depths"], [1])

    def test_diverging_next_address_does_not_merge(self) -> None:
        self.engine.process_sentence("눈꽃")
        result = self.engine.process_sentence("눈이")
        self.assertEqual(result["word_segments"], ["눈", "이"])

    def test_cell_elements_keep_side_view_coordinates(self) -> None:
        result = self.engine.process_sentence("눈꽃")
        sentence_id = result["sentence_id"]

        rows = self.engine.conn.execute(
            """
            SELECT sentence_id, pass_id, position
            FROM cell_elements
            WHERE sentence_id = ?
            ORDER BY pass_id, position
            """,
            (sentence_id,),
        ).fetchall()

        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0]["sentence_id"]), sentence_id)
        self.assertEqual(int(rows[0]["pass_id"]), 0)
        self.assertEqual(int(rows[0]["position"]), 0)

    def test_top_view_is_not_locked_to_current_pass_number(self) -> None:
        # Build a depth-1 Unit and store a pass-1 connection to a remaining Unit.
        self.engine.process_sentence("눈꽃A")
        self.engine.process_sentence("눈꽃B")
        third = self.engine.process_sentence("눈꽃C")
        self.assertEqual(third["word_segments"][0], "눈꽃")

        # Repeating the same sequence should be able to use the historical
        # pass-1 path even though top-view matching itself has no current-pass filter.
        fourth = self.engine.process_sentence("눈꽃C")
        self.assertEqual(fourth["word_segments"][0], "눈꽃")
        self.assertGreaterEqual(len(fourth["pass_results"]), 2)


if __name__ == "__main__":
    unittest.main()
