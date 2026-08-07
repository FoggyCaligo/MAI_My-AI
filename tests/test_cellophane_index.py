from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.engine import CognitiveEngine


class CellophaneIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "mai_test.db"
        self.engine = CognitiveEngine(self.db_path)

    def tearDown(self) -> None:
        self.engine.close()
        self.tmp.cleanup()

    def _chars(self, text: str) -> list[str]:
        return [self.engine.get_or_create_unit(ch) for ch in text]

    def test_same_sentence_path_survives_across_edges(self) -> None:
        units = self._chars("ABC")
        self.engine._ingest_cell_elements(units, pass_id=7, sentence_id="old")

        index = self.engine._load_cell_index(set(units))
        overlaps = self.engine._collect_span_overlaps(units, index)

        self.assertIn((0, 3), overlaps)
        self.assertEqual(overlaps[(0, 3)][1], 1)

    def test_different_sentences_are_not_stitched(self) -> None:
        a, b, c = self._chars("ABC")
        self.engine.conn.execute(
            """
            INSERT INTO cell_elements
                (source_unit_id, next_unit_id, sentence_id, position, pass_id, cell_depth)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (a, b, "sentence-a", 0, 0, 0),
        )
        self.engine.conn.execute(
            """
            INSERT INTO cell_elements
                (source_unit_id, next_unit_id, sentence_id, position, pass_id, cell_depth)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (b, c, "sentence-b", 1, 9, 0),
        )
        self.engine.conn.commit()

        units = [a, b, c]
        index = self.engine._load_cell_index(set(units))
        overlaps = self.engine._collect_span_overlaps(units, index)

        self.assertNotIn((0, 3), overlaps)

    def test_pass_id_does_not_block_same_path(self) -> None:
        a, b, c = self._chars("ABC")
        self.engine.conn.execute(
            """
            INSERT INTO cell_elements
                (source_unit_id, next_unit_id, sentence_id, position, pass_id, cell_depth)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (a, b, "sentence-x", 0, 1, 0),
        )
        self.engine.conn.execute(
            """
            INSERT INTO cell_elements
                (source_unit_id, next_unit_id, sentence_id, position, pass_id, cell_depth)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (b, c, "sentence-x", 1, 4, 0),
        )
        self.engine.conn.commit()

        units = [a, b, c]
        index = self.engine._load_cell_index(set(units))
        overlaps = self.engine._collect_span_overlaps(units, index)

        self.assertIn((0, 3), overlaps)

    def test_merged_depth_uses_actual_child_depths(self) -> None:
        a, b, c = self._chars("ABC")
        ab = self.engine.get_or_create_unit("AB", [a, b])
        abc = self.engine.get_or_create_unit("ABC", [ab, c])

        self.assertEqual(self.engine.get_unit_depth(a), 0)
        self.assertEqual(self.engine.get_unit_depth(ab), 1)
        self.assertEqual(self.engine.get_unit_depth(abc), 2)


if __name__ == "__main__":
    unittest.main()
