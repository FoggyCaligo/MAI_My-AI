from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path

from core.engine import CognitiveEngine, ThoughtConfig, ThoughtNode


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

    def test_footprint_includes_identity_and_every_composition_depth(self) -> None:
        ironId = self._unit("철")
        waterId = self._unit("수")
        topicId = self._unit("는")
        nameId = self.engine.get_or_create_unit("철수", [ironId, waterId])
        phraseId = self.engine.get_or_create_unit("철수는", [nameId, topicId])

        footprint = self.engine._unitFootprint(phraseId)

        self.assertEqual(footprint[phraseId], ("identity", 0))
        self.assertEqual(footprint[nameId], ("composition", 1))
        self.assertEqual(footprint[topicId], ("composition", 1))
        self.assertEqual(footprint[ironId], ("composition", 2))
        self.assertEqual(footprint[waterId], ("composition", 2))

    def test_thought_view_uses_sheet_local_depth_and_keeps_rejected_history(self) -> None:
        aId = self._unit("A")
        firstRoot = ThoughtNode(
            elementId="element-first-root",
            unitId=aId,
            parentElementId=None,
            branchId="branch-first",
            position=0,
            observedDensity=1,
            thoughtDensity=0,
            thoughtVisibility=0.0,
            opacity=1.0,
            status="conclusion",
        )
        firstRepeat = ThoughtNode(
            elementId="element-first-repeat",
            unitId=aId,
            parentElementId="element-first-root",
            branchId="branch-first",
            position=1,
            observedDensity=1,
            thoughtDensity=0,
            thoughtVisibility=0.0,
            opacity=1.0,
            status="conclusion",
        )
        second = ThoughtNode(
            elementId="element-second",
            unitId=aId,
            parentElementId=None,
            branchId="branch-second",
            position=0,
            observedDensity=1,
            thoughtDensity=0,
            thoughtVisibility=0.0,
            opacity=1.0,
            status="conclusion",
        )
        rejected = ThoughtNode(
            elementId="element-rejected",
            unitId=aId,
            parentElementId=None,
            branchId="branch-rejected",
            position=0,
            observedDensity=1,
            thoughtDensity=0,
            thoughtVisibility=0.0,
            opacity=1.0,
            status="rejected",
        )

        self.engine._persistThought(
            "thought-first",
            None,
            {firstRoot.elementId: firstRoot, firstRepeat.elementId: firstRepeat},
        )
        self.engine._persistThought(
            "thought-second",
            None,
            {second.elementId: second},
        )
        self.engine._persistThought(
            "thought-rejected",
            None,
            {rejected.elementId: rejected},
        )

        view = self.engine.getThoughtView()
        cell = next(item for item in view["cells"] if item["unit_id"] == aId)
        byElement = {
            item["element_id"]: item for item in cell["occurrences"]
        }

        self.assertEqual(byElement[second.elementId]["local_z_depth"], 0)
        self.assertEqual(byElement[firstRoot.elementId]["local_z_depth"], 1)
        self.assertEqual(byElement[firstRepeat.elementId]["local_z_depth"], 1)
        self.assertAlmostEqual(
            byElement[firstRoot.elementId]["visible_opacity"],
            0.8,
        )
        self.assertIsNone(byElement[rejected.elementId]["local_z_depth"])
        self.assertEqual(byElement[rejected.elementId]["visible_opacity"], 0.0)
        self.assertFalse(
            byElement[rejected.elementId]["participates_in_current_view"]
        )

    def test_recall_applies_source_cell_depth_to_outgoing_thought_edges(self) -> None:
        aId, bId, cId = [self._unit(content) for content in "ABC"]

        def persistPath(
            thoughtId: str,
            rootElementId: str,
            targetElementId: str,
            targetUnitId: str,
        ) -> None:
            root = ThoughtNode(
                elementId=rootElementId,
                unitId=aId,
                parentElementId=None,
                branchId=thoughtId,
                position=0,
                observedDensity=0,
                thoughtDensity=0,
                thoughtVisibility=0.0,
                opacity=1.0,
                status="conclusion",
            )
            target = ThoughtNode(
                elementId=targetElementId,
                unitId=targetUnitId,
                parentElementId=rootElementId,
                branchId=thoughtId,
                position=1,
                observedDensity=0,
                thoughtDensity=0,
                thoughtVisibility=0.0,
                opacity=1.0,
                status="conclusion",
            )
            self.engine._persistThought(
                thoughtId,
                None,
                {root.elementId: root, target.elementId: target},
            )

        persistPath("thought-ab", "element-a-old", "element-b", bId)
        persistPath("thought-ac", "element-a-new", "element-c", cId)

        result = self.engine.recall([aId])
        candidates = {item["unit_id"]: item for item in result["candidates"]}
        rankedIds = [item["unit_id"] for item in result["candidates"]]

        self.assertAlmostEqual(candidates[cId]["thought_visibility"], 1.0)
        self.assertAlmostEqual(candidates[bId]["thought_visibility"], 0.8)
        self.assertLess(rankedIds.index(cId), rankedIds.index(bId))


class ThoughtProjectionMigrationTests(unittest.TestCase):
    def test_existing_thoughts_receive_sequence_visibility_and_footprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dbPath = Path(tmp) / "legacy-thought.db"
            conn = sqlite3.connect(dbPath)
            conn.executescript(
                """
                CREATE TABLE units (
                    unit_id TEXT PRIMARY KEY,
                    depth INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    support_count INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE compositions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_unit_id TEXT NOT NULL,
                    child_unit_id TEXT NOT NULL,
                    position INTEGER NOT NULL
                );
                CREATE TABLE thoughts (
                    thought_id TEXT PRIMARY KEY,
                    source_sentence_id TEXT,
                    status TEXT NOT NULL DEFAULT 'complete',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE thought_elements (
                    element_id TEXT PRIMARY KEY,
                    thought_id TEXT NOT NULL,
                    unit_id TEXT NOT NULL,
                    thought_position INTEGER NOT NULL,
                    branch_id TEXT NOT NULL,
                    parent_element_id TEXT,
                    status TEXT NOT NULL,
                    observed_density INTEGER NOT NULL DEFAULT 0,
                    thought_density INTEGER NOT NULL DEFAULT 0,
                    opacity REAL NOT NULL DEFAULT 1.0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE thought_edges (
                    edge_id TEXT PRIMARY KEY,
                    thought_id TEXT NOT NULL,
                    source_element_id TEXT NOT NULL,
                    target_element_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    observed_density INTEGER NOT NULL DEFAULT 0,
                    thought_density INTEGER NOT NULL DEFAULT 0,
                    opacity REAL NOT NULL DEFAULT 1.0
                );
                INSERT INTO units (unit_id, depth, content) VALUES ('unit-a', 0, 'A');
                INSERT INTO thoughts (thought_id) VALUES ('thought-old');
                INSERT INTO thought_elements
                  (element_id, thought_id, unit_id, thought_position, branch_id, status)
                VALUES ('element-old', 'thought-old', 'unit-a', 0, 'branch-old', 'conclusion');
                """
            )
            conn.commit()
            conn.close()

            engine = CognitiveEngine(dbPath)
            try:
                thought = engine.conn.execute(
                    "SELECT thought_sequence FROM thoughts WHERE thought_id = 'thought-old'"
                ).fetchone()
                elementColumns = {
                    str(row["name"])
                    for row in engine.conn.execute(
                        "PRAGMA table_info(thought_elements)"
                    ).fetchall()
                }
                footprint = engine.conn.execute(
                    """
                    SELECT footprint_unit_id, footprint_kind, composition_distance
                    FROM thought_footprints
                    WHERE element_id = 'element-old'
                    """
                ).fetchone()

                self.assertEqual(int(thought["thought_sequence"]), 1)
                self.assertIn("thought_visibility", elementColumns)
                self.assertEqual(str(footprint["footprint_unit_id"]), "unit-a")
                self.assertEqual(str(footprint["footprint_kind"]), "identity")
                self.assertEqual(int(footprint["composition_distance"]), 0)
            finally:
                engine.close()


if __name__ == "__main__":
    unittest.main()
