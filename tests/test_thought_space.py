from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.engine import CognitiveEngine, ThoughtConfig


class ThoughtSpaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "mai_test.db"
        self.engine = CognitiveEngine(
            self.db_path,
            ThoughtConfig(
                recallLimit=5,
                workingMemoryCapacity=5,
                maxThoughtSteps=5,
                maxBranchesPerFocus=3,
                stableRepeatLimit=1,
            ),
        )

    def tearDown(self) -> None:
        self.engine.close()
        self.tmp.cleanup()

    def _unit(self, content: str) -> str:
        return self.engine.get_or_create_unit(content)

    def _edge(
        self,
        sourceId: str,
        targetId: str,
        sentenceId: str,
        position: int,
    ) -> None:
        self.engine.conn.execute(
            """
            INSERT INTO cell_elements
              (source_unit_id, next_unit_id, sentence_id, position, pass_id, link_depth)
            VALUES (?, ?, ?, ?, 0, 0)
            """,
            (sourceId, targetId, sentenceId, position),
        )
        self.engine.conn.commit()
        self.engine._cell_cache.clear()

    def test_single_unit_observed_layer_is_preserved(self) -> None:
        unitId = self._unit("완성")
        self.engine._record_cell_elements([unitId], 3, "sentence-one")

        self.assertEqual(
            self.engine._load_observed_layer("sentence-one", 3),
            [unitId],
        )

    def test_terminal_merged_unit_is_stored_as_its_own_layer(self) -> None:
        self.engine.process_sentence("AB")
        result = self.engine.process_sentence("AB")
        mergedId = result["pass_results"][-1]["unit_ids"][0]

        rows = self.engine.conn.execute(
            """
            SELECT pass_id, position, unit_id
            FROM observed_layer_units
            WHERE sentence_id = ? AND unit_id = ?
            """,
            (result["sentence_id"], mergedId),
        ).fetchall()

        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["position"]), 0)

    def test_recall_only_returns_direct_one_hop_neighbor(self) -> None:
        aId, bId, cId = [self._unit(content) for content in "ABC"]
        self._edge(aId, bId, "sentence-ab", 0)
        self._edge(bId, cId, "sentence-bc", 0)

        result = self.engine.recall([aId])
        recalledIds = [candidate["unit_id"] for candidate in result["candidates"]]

        self.assertIn(bId, recalledIds)
        self.assertNotIn(cId, recalledIds)

    def test_thought_persists_conclusion_and_unselected_alternative(self) -> None:
        aId, bId, cId, dId, eId = [self._unit(content) for content in "ABCDE"]
        self._edge(bId, eId, "sentence-be", 0)
        self._edge(cId, dId, "sentence-cd", 0)
        recallResult = {
            "active_unit_ids": [],
            "candidates": [
                {
                    "unit_id": aId,
                    "content": "A",
                    "depth": 0,
                    "observed_density": 5,
                    "thought_density": 0,
                    "opacity": 5.0,
                    "sources": ["observed"],
                },
                {
                    "unit_id": bId,
                    "content": "B",
                    "depth": 0,
                    "observed_density": 3,
                    "thought_density": 0,
                    "opacity": 3.0,
                    "sources": ["observed"],
                },
                {
                    "unit_id": cId,
                    "content": "C",
                    "depth": 0,
                    "observed_density": 1,
                    "thought_density": 0,
                    "opacity": 1.0,
                    "sources": ["observed"],
                },
            ],
        }

        result = self.engine.think(recallResult, sourceSentenceId="source")
        statuses = {node["unit_id"]: node["status"] for node in result["nodes"]}
        stored = self.engine.conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM thought_elements
            WHERE thought_id = ?
            """,
            (result["thought_id"],),
        ).fetchone()["n"]

        self.assertGreaterEqual(int(stored), 3)
        self.assertEqual(statuses[aId], "conclusion")
        self.assertEqual(statuses[bId], "conclusion")
        self.assertEqual(statuses[cId], "alternative")

    def test_alternative_is_only_recalled_when_allowed(self) -> None:
        aId, bId, cId, dId, eId = [self._unit(content) for content in "ABCDE"]
        self._edge(bId, eId, "sentence-be", 0)
        self._edge(cId, dId, "sentence-cd", 0)
        recallResult = {
            "active_unit_ids": [],
            "candidates": [
                {
                    "unit_id": aId,
                    "observed_density": 5,
                    "thought_density": 0,
                    "opacity": 5.0,
                },
                {
                    "unit_id": bId,
                    "observed_density": 3,
                    "thought_density": 0,
                    "opacity": 3.0,
                },
                {
                    "unit_id": cId,
                    "observed_density": 1,
                    "thought_density": 0,
                    "opacity": 1.0,
                },
            ],
        }
        self.engine.think(recallResult)

        externalRecall = self.engine.recall([aId], allowAlternativeThoughts=True)
        internalRecall = self.engine.recall([aId], allowAlternativeThoughts=False)
        externalIds = {item["unit_id"] for item in externalRecall["candidates"]}
        internalIds = {item["unit_id"] for item in internalRecall["candidates"]}

        self.assertIn(bId, externalIds)
        self.assertIn(cId, externalIds)
        self.assertIn(bId, internalIds)
        self.assertNotIn(cId, internalIds)

    def test_expression_uses_unit_positions_and_replaces_multiple_slots(self) -> None:
        motherId = self._unit("엄마")
        fatherId = self._unit("아빠")
        homeId = self._unit("집")
        companyId = self._unit("회사")
        topicId = self._unit("는")
        atId = self._unit("에")
        existsId = self._unit("있다")

        self.engine.get_or_create_unit("사람", [motherId, fatherId])
        self.engine.get_or_create_unit("장소", [homeId, companyId])
        self.engine._record_cell_elements(
            [fatherId, topicId, companyId, atId, existsId],
            1,
            "sentence-father",
        )
        self.engine._record_cell_elements(
            [motherId, topicId, homeId, atId, existsId],
            1,
            "sentence-mother",
        )

        result = self.engine.express(
            {"conclusion_unit_ids": [motherId, homeId]},
        )

        self.assertEqual(result["text"], "엄마는집에있다")
        self.assertEqual(result["unit_ids"][0], motherId)
        self.assertEqual(result["unit_ids"][2], homeId)
        self.assertEqual(len(result["unit_ids"]), 5)


if __name__ == "__main__":
    unittest.main()
