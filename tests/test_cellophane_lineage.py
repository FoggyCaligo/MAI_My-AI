from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.engine import CognitiveEngine


class CellophaneLineageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "mai_test.db"
        self.engine = CognitiveEngine(self.db_path)

    def tearDown(self) -> None:
        self.engine.close()
        self.tmp.cleanup()

    def _chars(self, text: str) -> list[str]:
        return [self.engine.get_or_create_unit(ch) for ch in text]

    def _insert_edge(
        self,
        source: str,
        target: str,
        sentence_id: str,
        position: int,
        pass_id: int,
    ) -> None:
        self.engine.conn.execute(
            """
            INSERT INTO cell_elements
              (source_unit_id, next_unit_id, sentence_id, position, pass_id, link_depth)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (source, target, sentence_id, position, pass_id, 0),
        )
        self.engine.conn.commit()
        self.engine._cell_cache.clear()

    def test_different_sentences_are_not_stitched_into_one_unit(self) -> None:
        a, b, c = self._chars("ABC")
        self._insert_edge(a, b, "sentence-a", 0, 0)
        self._insert_edge(b, c, "sentence-b", 1, 0)

        result = self.engine.process_sentence("ABC")

        self.assertEqual(result["word_segments"], ["AB", "C"])
        self.assertNotEqual(result["word_segments"], ["ABC"])

    def test_different_historical_passes_are_not_stitched(self) -> None:
        a, b, c = self._chars("ABC")
        self._insert_edge(a, b, "sentence-x", 0, 1)
        self._insert_edge(b, c, "sentence-x", 1, 4)

        result = self.engine.process_sentence("ABC")

        self.assertEqual(result["word_segments"], ["AB", "C"])
        self.assertNotEqual(result["word_segments"], ["ABC"])

    def test_same_historical_sheet_survives_and_density_is_literal_count(self) -> None:
        a, b, c = self._chars("ABC")
        self._insert_edge(a, b, "sentence-x", 0, 7)
        self._insert_edge(b, c, "sentence-x", 1, 7)

        survivors = self.engine._matching_paths(a, b)
        self.assertEqual(len(survivors), 1)

        survivors = self.engine._advance_paths(survivors, b, c)
        self.assertEqual(len(survivors), 1)

        result = self.engine.process_sentence("ABC")
        self.assertEqual(result["word_segments"], ["ABC"])

    def test_merged_depth_uses_actual_child_depths(self) -> None:
        a, b, c = self._chars("ABC")
        ab = self.engine.get_or_create_unit("AB", [a, b])
        abc = self.engine.get_or_create_unit("ABC", [ab, c])

        self.assertEqual(self.engine.get_unit_depth(a), 0)
        self.assertEqual(self.engine.get_unit_depth(ab), 1)
        self.assertEqual(self.engine.get_unit_depth(abc), 2)


class LegacyMigrationTests(unittest.TestCase):
    def test_partial_legacy_migration_only_copies_missing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE units (
                    unit_id TEXT PRIMARY KEY,
                    depth INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    support_count INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE unit_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_unit_id TEXT NOT NULL,
                    target_unit_id TEXT NOT NULL,
                    sentence_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    pass_id INTEGER NOT NULL,
                    link_depth INTEGER NOT NULL,
                    opacity REAL NOT NULL DEFAULT 1.0
                );
                """
            )
            conn.executemany(
                "INSERT INTO units (unit_id, depth, content) VALUES (?, 0, ?)",
                [("a", "A"), ("b", "B"), ("c", "C")],
            )
            conn.executemany(
                """
                INSERT INTO unit_links
                  (source_unit_id, target_unit_id, sentence_id, position, pass_id, link_depth)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                [
                    ("a", "b", "s1", 0, 0),
                    ("b", "c", "s1", 1, 0),
                ],
            )
            conn.commit()
            conn.close()

            first = CognitiveEngine(db_path)
            first.conn.execute(
                "DELETE FROM cell_elements WHERE source_unit_id = 'b' AND next_unit_id = 'c'"
            )
            first.conn.commit()
            first.close()

            reopened = CognitiveEngine(db_path)
            count = reopened.conn.execute(
                "SELECT COUNT(*) AS n FROM cell_elements WHERE sentence_id = 's1'"
            ).fetchone()["n"]
            reopened.close()

            self.assertEqual(int(count), 2)


if __name__ == "__main__":
    unittest.main()
