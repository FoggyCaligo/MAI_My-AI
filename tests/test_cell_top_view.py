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
        self.assertEqual(second["pass_results"][0]["top_view_densities"], [1])

    def test_diverging_next_address_does_not_merge(self) -> None:
        self.engine.process_sentence("눈꽃")
        result = self.engine.process_sentence("눈이")
        self.assertEqual(result["word_segments"], ["눈", "이"])
        self.assertEqual(result["pass_results"][0]["top_view_densities"], [0])

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

    def test_density_counts_all_matching_structural_observations_across_passes(self) -> None:
        self.engine.process_sentence("AB")
        self.engine.process_sentence("AB")

        a_id = self.engine.conn.execute(
            "SELECT unit_id FROM units WHERE depth = 0 AND content = 'A'"
        ).fetchone()["unit_id"]
        b_id = self.engine.conn.execute(
            "SELECT unit_id FROM units WHERE depth = 0 AND content = 'B'"
        ).fetchone()["unit_id"]

        self.assertEqual(self.engine._edge_density(str(a_id), str(b_id)), 2)

    def test_old_sentence_is_lazily_projected_with_new_units_without_rewrite(self) -> None:
        original = self.engine.process_sentence("눈꽃의계절")
        original_id = original["sentence_id"]
        self.assertEqual(original["word_segments"], ["눈", "꽃", "의", "계", "절"])

        before_rows = self.engine.conn.execute(
            "SELECT COUNT(*) AS n FROM cell_elements WHERE sentence_id = ?",
            (original_id,),
        ).fetchone()["n"]

        snow = self.engine.process_sentence("눈꽃이 흩날린다")
        self.assertEqual(snow["word_segments"][0], "눈꽃")

        season = self.engine.process_sentence("독서의 계절")
        self.assertIn("계절", season["word_segments"])

        active = self.engine.activate_sentence(original_id)
        self.assertGreaterEqual(len(active["layers"]), 2)
        self.assertEqual(active["layers"][-1]["contents"], ["눈꽃", "의", "계절"])

        after_rows = self.engine.conn.execute(
            "SELECT COUNT(*) AS n FROM cell_elements WHERE sentence_id = ?",
            (original_id,),
        ).fetchone()["n"]
        self.assertEqual(before_rows, after_rows)

    def test_activation_is_read_only(self) -> None:
        original = self.engine.process_sentence("ABCD")
        sentence_id = original["sentence_id"]

        before_units = self.engine.conn.execute("SELECT COUNT(*) AS n FROM units").fetchone()["n"]
        before_cells = self.engine.conn.execute("SELECT COUNT(*) AS n FROM cell_elements").fetchone()["n"]

        self.engine.activate_sentence(sentence_id)

        after_units = self.engine.conn.execute("SELECT COUNT(*) AS n FROM units").fetchone()["n"]
        after_cells = self.engine.conn.execute("SELECT COUNT(*) AS n FROM cell_elements").fetchone()["n"]
        self.assertEqual(before_units, after_units)
        self.assertEqual(before_cells, after_cells)


if __name__ == "__main__":
    unittest.main()
