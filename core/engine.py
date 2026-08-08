from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Database

LayerKey = tuple[str, int, int]  # (sentence_id, stored_pass_id, position)
CompositionCandidate = tuple[str, tuple[str, ...], int]  # parent_id, children, depth


@dataclass(slots=True)
class CellElement:
    next_unit_id: str
    sentence_id: str
    pass_id: int
    position: int
    opacity: float


@dataclass(slots=True)
class ThoughtConfig:
    recallLimit: int = 5
    workingMemoryCapacity: int = 5
    maxThoughtSteps: int = 20
    maxBranchesPerFocus: int = 3
    stableRepeatLimit: int = 2


@dataclass(slots=True)
class ThoughtNode:
    elementId: str
    unitId: str
    parentElementId: str | None
    branchId: str
    position: int
    observedDensity: int
    thoughtDensity: int
    opacity: float
    status: str = "supported"
    lastActivatedStep: int = 0
    explored: bool = False


class CognitiveEngine:
    """Recursive MAI engine built around logical cellophane cells.

    Persistent observations are never retroactively rewritten.

    Top view:
      each source Unit is a logical cell containing observed next-unit addresses.
      For the currently active sequence, unrelated historical sheets are filtered
      out by sentence/pass/position continuity first. The number of surviving
      CellElements is the density itself.

    Lazy projection:
      higher-depth Units live independently from the observations that first
      produced their children. When an old sentence becomes active again,
      existing compositions are overlaid on that active sequence without
      rewriting the old observation.

    Side view:
      sentence_id + pass_id + position preserve observed layers, while
      compositions preserve vertical parent/child structure.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        thoughtConfig: ThoughtConfig | None = None,
    ) -> None:
        self.db = Database(db_path)
        self.conn = self.db.conn
        self._cell_cache: dict[str, dict[str, dict[LayerKey, float]]] = {}
        self._composition_cache: dict[str, list[CompositionCandidate]] = {}
        self.thoughtConfig = thoughtConfig or ThoughtConfig()

    def close(self) -> None:
        self.db.close()

    def get_unit_depth(self, unit_id: str) -> int:
        row = self.conn.execute(
            "SELECT depth FROM units WHERE unit_id = ?", (unit_id,)
        ).fetchone()
        return int(row["depth"]) if row else 0

    def get_unit_content(self, unit_id: str) -> str:
        row = self.conn.execute(
            "SELECT content FROM units WHERE unit_id = ?", (unit_id,)
        ).fetchone()
        return str(row["content"]) if row else unit_id

    def _composition_exists(self, parent_id: str, child_ids: list[str]) -> bool:
        rows = self.conn.execute(
            """
            SELECT child_unit_id
            FROM compositions
            WHERE parent_unit_id = ?
            ORDER BY position
            """,
            (parent_id,),
        ).fetchall()
        existing = [str(row["child_unit_id"]) for row in rows]
        return existing == child_ids

    def _record_composition(self, parent_id: str, child_ids: list[str]) -> None:
        self.conn.executemany(
            """
            INSERT INTO compositions (parent_unit_id, child_unit_id, position)
            VALUES (?, ?, ?)
            """,
            [(parent_id, child_id, pos) for pos, child_id in enumerate(child_ids)],
        )
        self._composition_cache.clear()

    def get_or_create_unit(
        self,
        content: str,
        child_unit_ids: list[str] | None = None,
    ) -> str:
        if not child_unit_ids:
            depth = 0
        else:
            depth = max(self.get_unit_depth(uid) for uid in child_unit_ids) + 1

        candidates = self.conn.execute(
            "SELECT unit_id FROM units WHERE depth = ? AND content = ?",
            (depth, content),
        ).fetchall()

        if not child_unit_ids:
            if candidates:
                return str(candidates[0]["unit_id"])
        else:
            for row in candidates:
                unit_id = str(row["unit_id"])
                if self._composition_exists(unit_id, child_unit_ids):
                    return unit_id

        unit_id = f"unit-d{depth}-{uuid.uuid4().hex[:8]}"
        self.conn.execute(
            "INSERT INTO units (unit_id, depth, content, support_count) VALUES (?, ?, ?, 1)",
            (unit_id, depth, content),
        )
        if child_unit_ids:
            self._record_composition(unit_id, child_unit_ids)
        self.conn.commit()
        return unit_id

    def _cell_targets(self, source_unit_id: str) -> dict[str, dict[LayerKey, float]]:
        cached = self._cell_cache.get(source_unit_id)
        if cached is not None:
            return cached

        targets: dict[str, dict[LayerKey, float]] = {}
        rows = self.conn.execute(
            """
            SELECT next_unit_id, sentence_id, pass_id, position, opacity
            FROM cell_elements
            WHERE source_unit_id = ?
            """,
            (source_unit_id,),
        ).fetchall()
        for row in rows:
            next_id = str(row["next_unit_id"])
            key: LayerKey = (
                str(row["sentence_id"]),
                int(row["pass_id"]),
                int(row["position"]),
            )
            targets.setdefault(next_id, {})[key] = float(row["opacity"])

        self._cell_cache[source_unit_id] = targets
        return targets

    def _edge_density(
        self,
        source_unit_id: str,
        next_unit_id: str,
        exclude_sentence_id: str | None = None,
    ) -> int:
        paths = self._cell_targets(source_unit_id).get(next_unit_id, {})
        if exclude_sentence_id is None:
            return len(paths)
        return sum(1 for key in paths if key[0] != exclude_sentence_id)

    def _matching_paths(
        self,
        source_unit_id: str,
        next_unit_id: str,
        exclude_sentence_id: str | None = None,
    ) -> dict[LayerKey, float]:
        paths = self._cell_targets(source_unit_id).get(next_unit_id, {})
        if exclude_sentence_id is None:
            return dict(paths)
        return {
            key: opacity
            for key, opacity in paths.items()
            if key[0] != exclude_sentence_id
        }

    def _advance_paths(
        self,
        survivors: dict[LayerKey, float],
        source_unit_id: str,
        next_unit_id: str,
    ) -> dict[LayerKey, float]:
        """Keep only the same historical sheet at the immediately next position."""
        if not survivors:
            return {}

        next_paths = self._cell_targets(source_unit_id).get(next_unit_id, {})
        advanced: dict[LayerKey, float] = {}
        for (sentence_id, stored_pass_id, position), opacity in survivors.items():
            wanted = (sentence_id, stored_pass_id, position + 1)
            next_opacity = next_paths.get(wanted)
            if next_opacity is not None:
                advanced[wanted] = min(opacity, next_opacity)
        return advanced

    def _record_cell_elements(
        self,
        unit_ids: list[str],
        pass_id: int,
        sentence_id: str,
    ) -> None:
        self._record_observed_layer(unit_ids, pass_id, sentence_id)
        if len(unit_ids) < 2:
            return

        rows: list[tuple[str, str, str, int, int, int]] = []
        for position in range(len(unit_ids) - 1):
            source = unit_ids[position]
            next_id = unit_ids[position + 1]
            link_depth = max(self.get_unit_depth(source), self.get_unit_depth(next_id))
            rows.append((source, next_id, sentence_id, position, pass_id, link_depth))

        self.conn.executemany(
            """
            INSERT INTO cell_elements
              (source_unit_id, next_unit_id, sentence_id, position, pass_id, link_depth)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()

        for source, next_id, sid, position, stored_pass_id, _ in rows:
            cached = self._cell_cache.get(source)
            if cached is not None:
                cached.setdefault(next_id, {})[(sid, stored_pass_id, position)] = 1.0

    def _record_observed_layer(
        self,
        unitIds: list[str],
        passId: int,
        sentenceId: str,
    ) -> None:
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO observed_layer_units
              (sentence_id, pass_id, position, unit_id)
            VALUES (?, ?, ?, ?)
            """,
            [
                (sentenceId, passId, position, unitId)
                for position, unitId in enumerate(unitIds)
            ],
        )
        self.conn.commit()

    def _composition_candidates(self, first_child_id: str) -> list[CompositionCandidate]:
        cached = self._composition_cache.get(first_child_id)
        if cached is not None:
            return cached

        parent_rows = self.conn.execute(
            """
            SELECT DISTINCT c.parent_unit_id, u.depth
            FROM compositions c
            JOIN units u ON u.unit_id = c.parent_unit_id
            WHERE c.child_unit_id = ? AND c.position = 0
            """,
            (first_child_id,),
        ).fetchall()

        candidates: list[CompositionCandidate] = []
        for parent_row in parent_rows:
            parent_id = str(parent_row["parent_unit_id"])
            child_rows = self.conn.execute(
                """
                SELECT child_unit_id
                FROM compositions
                WHERE parent_unit_id = ?
                ORDER BY position
                """,
                (parent_id,),
            ).fetchall()
            children = tuple(str(row["child_unit_id"]) for row in child_rows)
            if children:
                candidates.append((parent_id, children, int(parent_row["depth"])))

        candidates.sort(key=lambda item: (item[2], len(item[1])), reverse=True)
        self._composition_cache[first_child_id] = candidates
        return candidates

    def _project_existing_units(self, unit_ids: list[str]) -> list[str]:
        if len(unit_ids) <= 1:
            return list(unit_ids)

        projected: list[str] = []
        index = 0
        while index < len(unit_ids):
            matched_parent: str | None = None
            matched_length = 0

            for parent_id, children, _ in self._composition_candidates(unit_ids[index]):
                end = index + len(children)
                if end > len(unit_ids):
                    continue
                if tuple(unit_ids[index:end]) == children:
                    matched_parent = parent_id
                    matched_length = len(children)
                    break

            if matched_parent is None:
                projected.append(unit_ids[index])
                index += 1
            else:
                projected.append(matched_parent)
                index += matched_length

        return projected

    def _project_until_stable(self, unit_ids: list[str]) -> list[list[str]]:
        layers: list[list[str]] = []
        current = list(unit_ids)
        for _ in range(100):
            projected = self._project_existing_units(current)
            if projected == current:
                break
            layers.append(projected)
            current = projected
            if len(current) == 1:
                break
        return layers

    def _load_observed_layer(self, sentence_id: str, pass_id: int = 0) -> list[str]:
        slotRows = self.conn.execute(
            """
            SELECT unit_id
            FROM observed_layer_units
            WHERE sentence_id = ? AND pass_id = ?
            ORDER BY position
            """,
            (sentence_id, pass_id),
        ).fetchall()
        if slotRows:
            return [str(row["unit_id"]) for row in slotRows]

        rows = self.conn.execute(
            """
            SELECT source_unit_id, next_unit_id, position
            FROM cell_elements
            WHERE sentence_id = ? AND pass_id = ?
            ORDER BY position
            """,
            (sentence_id, pass_id),
        ).fetchall()
        if not rows:
            return []

        result = [str(row["source_unit_id"]) for row in rows]
        result.append(str(rows[-1]["next_unit_id"]))
        return result

    def activate_sentence(self, sentence_id: str) -> dict[str, Any]:
        base_ids = self._load_observed_layer(sentence_id, 0)
        projection_layers = self._project_until_stable(base_ids)

        layers: list[dict[str, Any]] = []
        if base_ids:
            layers.append(
                {
                    "level": 0,
                    "unit_ids": base_ids,
                    "contents": [self.get_unit_content(uid) for uid in base_ids],
                    "depths": [self.get_unit_depth(uid) for uid in base_ids],
                }
            )
        for level, projected in enumerate(projection_layers, start=1):
            layers.append(
                {
                    "level": level,
                    "unit_ids": projected,
                    "contents": [self.get_unit_content(uid) for uid in projected],
                    "depths": [self.get_unit_depth(uid) for uid in projected],
                }
            )

        return {"sentence_id": sentence_id, "layers": layers}

    def _discover_from_density(
        self,
        unit_ids: list[str],
        current_sentence_id: str,
    ) -> tuple[list[str], list[int]]:
        """Filter historical sheets first, then use survivor count as density."""
        if len(unit_ids) <= 1:
            return list(unit_ids), []

        edge_densities = [
            self._edge_density(unit_ids[i], unit_ids[i + 1], current_sentence_id)
            for i in range(len(unit_ids) - 1)
        ]

        result: list[str] = []
        start = 0
        while start < len(unit_ids):
            if start == len(unit_ids) - 1:
                result.append(unit_ids[start])
                break

            survivors = self._matching_paths(
                unit_ids[start], unit_ids[start + 1], current_sentence_id
            )
            if not survivors:
                result.append(unit_ids[start])
                start += 1
                continue

            best_end = start + 2
            best_density = len(survivors)
            cursor = start + 1
            while cursor < len(unit_ids) - 1:
                survivors = self._advance_paths(
                    survivors, unit_ids[cursor], unit_ids[cursor + 1]
                )
                if not survivors:
                    break
                end = cursor + 2
                density = len(survivors)
                if density > best_density or (
                    density == best_density and end > best_end
                ):
                    best_density = density
                    best_end = end
                cursor += 1

            child_ids = unit_ids[start:best_end]
            content = "".join(self.get_unit_content(uid) for uid in child_ids)
            result.append(self.get_or_create_unit(content, child_ids))
            start = best_end

        return result, edge_densities

    def apply_feedback(self, sentence_id: str, score: float) -> float:
        clamped = max(0.0, min(100.0, float(score)))
        multiplier = 1.0 + (clamped - 50.0) * 0.005

        rows = self.conn.execute(
            """
            SELECT opacity
            FROM cell_elements
            WHERE sentence_id = ? AND link_depth >= 2
            """,
            (sentence_id,),
        ).fetchall()
        if not rows:
            return 1.0

        avg = sum(float(row["opacity"]) for row in rows) / len(rows)
        new_avg = max(0.01, min(10.0, avg * multiplier))

        self.conn.execute(
            """
            UPDATE cell_elements
            SET opacity = MAX(0.01, MIN(10.0, opacity * ?))
            WHERE sentence_id = ? AND link_depth >= 2
            """,
            (multiplier, sentence_id),
        )
        self.conn.commit()
        self._cell_cache.clear()
        return new_avg

    def recall(
        self,
        activeUnitIds: list[str],
        *,
        excludeSentenceId: str | None = None,
        limit: int | None = None,
        allowAlternativeThoughts: bool = True,
    ) -> dict[str, Any]:
        """Read direct one-hop X/Y and eligible Z neighbors with their evidence."""
        activeIds = list(dict.fromkeys(activeUnitIds))
        if not activeIds:
            return {"active_unit_ids": [], "candidates": []}

        evidence: dict[str, dict[str, Any]] = {}

        def addEvidence(
            unitId: str,
            observedDensity: int = 0,
            thoughtDensity: int = 0,
            opacity: float = 0.0,
            source: str = "observed",
        ) -> None:
            if unitId in activeIds:
                return
            item = evidence.setdefault(
                unitId,
                {
                    "unit_id": unitId,
                    "observed_density": 0,
                    "thought_density": 0,
                    "opacity": 0.0,
                    "sources": set(),
                },
            )
            item["observed_density"] += int(observedDensity)
            item["thought_density"] += int(thoughtDensity)
            item["opacity"] += float(opacity)
            item["sources"].add(source)

        placeholders = ",".join("?" for _ in activeIds)
        sentenceFilter = ""
        params: list[Any] = activeIds + activeIds
        if excludeSentenceId is not None:
            sentenceFilter = "AND sentence_id != ?"
            params.append(excludeSentenceId)

        rows = self.conn.execute(
            f"""
            SELECT source_unit_id, next_unit_id,
                   COUNT(*) AS density, SUM(opacity) AS opacity_sum
            FROM cell_elements
            WHERE (source_unit_id IN ({placeholders})
                   OR next_unit_id IN ({placeholders}))
              {sentenceFilter}
            GROUP BY source_unit_id, next_unit_id
            """,
            params,
        ).fetchall()
        for row in rows:
            sourceId = str(row["source_unit_id"])
            targetId = str(row["next_unit_id"])
            otherId = targetId if sourceId in activeIds else sourceId
            addEvidence(
                otherId,
                observedDensity=int(row["density"]),
                opacity=float(row["opacity_sum"] or 0.0),
            )

        parentRows = self.conn.execute(
            f"""
            SELECT c.parent_unit_id, c.child_unit_id
            FROM compositions AS c
            WHERE c.child_unit_id IN ({placeholders})
            """,
            activeIds,
        ).fetchall()
        parentIds = list({str(row["parent_unit_id"]) for row in parentRows})
        for parentId in parentIds:
            addEvidence(parentId, observedDensity=1, opacity=1.0, source="composition")

        statuses = ["conclusion"]
        if allowAlternativeThoughts:
            statuses.append("alternative")
        statusPlaceholders = ",".join("?" for _ in statuses)
        thoughtRows = self.conn.execute(
            f"""
            SELECT sourceUnit.unit_id AS source_unit_id,
                   targetUnit.unit_id AS target_unit_id,
                   COUNT(*) AS density,
                   SUM(te.opacity) AS opacity_sum
            FROM thought_edges AS te
            JOIN thought_elements AS sourceUnit
              ON sourceUnit.element_id = te.source_element_id
            JOIN thought_elements AS targetUnit
              ON targetUnit.element_id = te.target_element_id
            WHERE sourceUnit.unit_id IN ({placeholders})
              AND te.status IN ({statusPlaceholders})
            GROUP BY sourceUnit.unit_id, targetUnit.unit_id
            """,
            activeIds + statuses,
        ).fetchall()
        for row in thoughtRows:
            addEvidence(
                str(row["target_unit_id"]),
                thoughtDensity=int(row["density"]),
                opacity=float(row["opacity_sum"] or 0.0),
                source="thought",
            )

        candidates: list[dict[str, Any]] = []
        for item in evidence.values():
            item["content"] = self.get_unit_content(str(item["unit_id"]))
            item["depth"] = self.get_unit_depth(str(item["unit_id"]))
            item["sources"] = sorted(item["sources"])
            candidates.append(item)

        candidates.sort(
            key=lambda item: (
                int(item["observed_density"]),
                int(item["thought_density"]),
                float(item["opacity"]),
                int(item["depth"]),
                str(item["unit_id"]),
            ),
            reverse=True,
        )
        resultLimit = limit if limit is not None else self.thoughtConfig.recallLimit
        return {
            "active_unit_ids": activeIds,
            "candidates": candidates[:resultLimit],
        }

    def _nodePath(self, node: ThoughtNode, nodes: dict[str, ThoughtNode]) -> list[ThoughtNode]:
        path: list[ThoughtNode] = []
        current: ThoughtNode | None = node
        while current is not None:
            path.append(current)
            current = (
                nodes.get(current.parentElementId)
                if current.parentElementId is not None
                else None
            )
        path.reverse()
        return path

    def _persistThought(
        self,
        thoughtId: str,
        sourceSentenceId: str | None,
        nodes: dict[str, ThoughtNode],
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO thoughts (thought_id, source_sentence_id, status)
            VALUES (?, ?, 'complete')
            """,
            (thoughtId, sourceSentenceId),
        )
        self.conn.executemany(
            """
            INSERT INTO thought_elements
              (element_id, thought_id, unit_id, thought_position, branch_id,
               parent_element_id, status, observed_density, thought_density, opacity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    node.elementId,
                    thoughtId,
                    node.unitId,
                    node.position,
                    node.branchId,
                    node.parentElementId,
                    node.status,
                    node.observedDensity,
                    node.thoughtDensity,
                    node.opacity,
                )
                for node in nodes.values()
            ],
        )
        edgeRows = []
        for node in nodes.values():
            if node.parentElementId is None:
                continue
            edgeRows.append(
                (
                    f"thought-edge-{uuid.uuid4().hex[:10]}",
                    thoughtId,
                    node.parentElementId,
                    node.elementId,
                    node.status,
                    node.observedDensity,
                    node.thoughtDensity,
                    node.opacity,
                )
            )
        self.conn.executemany(
            """
            INSERT INTO thought_edges
              (edge_id, thought_id, source_element_id, target_element_id, status,
               observed_density, thought_density, opacity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            edgeRows,
        )
        self.conn.commit()

    def think(
        self,
        recallResult: dict[str, Any],
        *,
        sourceSentenceId: str | None = None,
    ) -> dict[str, Any]:
        """Expand recalled Units on persistent Z, keeping X/Y Unit identity unchanged."""
        candidates = list(recallResult.get("candidates", []))
        thoughtId = f"thought-{uuid.uuid4().hex[:10]}"
        if not candidates:
            return {
                "thought_id": thoughtId,
                "nodes": [],
                "edges": [],
                "conclusion_unit_ids": [],
                "steps": [],
            }

        nodes: dict[str, ThoughtNode] = {}
        workingIds: list[str] = []
        rootCandidate = candidates[0]
        rootId = f"thought-element-{uuid.uuid4().hex[:10]}"
        root = ThoughtNode(
            elementId=rootId,
            unitId=str(rootCandidate["unit_id"]),
            parentElementId=None,
            branchId="branch-0",
            position=0,
            observedDensity=int(rootCandidate["observed_density"]),
            thoughtDensity=int(rootCandidate["thought_density"]),
            opacity=float(rootCandidate["opacity"]),
        )
        nodes[rootId] = root
        workingIds.append(rootId)
        directRootRecall = self.recall(
            [root.unitId],
            excludeSentenceId=sourceSentenceId,
            limit=self.thoughtConfig.recallLimit,
            allowAlternativeThoughts=False,
        )
        directRootIds = {
            str(candidate["unit_id"])
            for candidate in directRootRecall["candidates"]
        }

        for index, candidate in enumerate(
            candidates[1 : self.thoughtConfig.maxBranchesPerFocus + 1],
            start=1,
        ):
            elementId = f"thought-element-{uuid.uuid4().hex[:10]}"
            node = ThoughtNode(
                elementId=elementId,
                unitId=str(candidate["unit_id"]),
                parentElementId=rootId,
                branchId=f"branch-{index}",
                position=1,
                observedDensity=int(candidate["observed_density"]),
                thoughtDensity=int(candidate["thought_density"]),
                opacity=float(candidate["opacity"]),
                status=(
                    "supported"
                    if str(candidate["unit_id"]) in directRootIds
                    else "candidate"
                ),
            )
            nodes[elementId] = node
            workingIds.append(elementId)

        steps: list[dict[str, Any]] = []
        previousSignature: tuple[str, ...] | None = None
        stableCount = 0

        for step in range(self.thoughtConfig.maxThoughtSteps):
            activeNodes = [
                nodes[elementId]
                for elementId in workingIds
                if nodes[elementId].status != "evicted"
            ]
            unexplored = [node for node in activeNodes if not node.explored]
            if not unexplored:
                break
            focus = max(
                unexplored,
                key=lambda node: (
                    node.observedDensity,
                    node.thoughtDensity,
                    node.opacity,
                    node.lastActivatedStep,
                ),
            )
            focus.explored = True
            focus.lastActivatedStep = step
            neighborResult = self.recall(
                [focus.unitId],
                excludeSentenceId=sourceSentenceId,
                limit=self.thoughtConfig.maxBranchesPerFocus,
                allowAlternativeThoughts=False,
            )
            pathUnitIds = {node.unitId for node in self._nodePath(focus, nodes)}
            addedIds: list[str] = []
            for branchIndex, candidate in enumerate(neighborResult["candidates"]):
                candidateId = str(candidate["unit_id"])
                if candidateId in pathUnitIds:
                    continue
                duplicate = any(
                    node.parentElementId == focus.elementId and node.unitId == candidateId
                    for node in nodes.values()
                )
                if duplicate:
                    continue
                elementId = f"thought-element-{uuid.uuid4().hex[:10]}"
                branchId = (
                    focus.branchId
                    if branchIndex == 0
                    else f"branch-{uuid.uuid4().hex[:8]}"
                )
                child = ThoughtNode(
                    elementId=elementId,
                    unitId=candidateId,
                    parentElementId=focus.elementId,
                    branchId=branchId,
                    position=focus.position + 1,
                    observedDensity=int(candidate["observed_density"]),
                    thoughtDensity=int(candidate["thought_density"]),
                    opacity=float(candidate["opacity"]),
                    lastActivatedStep=step,
                )
                nodes[elementId] = child
                workingIds.append(elementId)
                addedIds.append(elementId)
            if addedIds and focus.status == "candidate":
                focus.status = "supported"

            while len(
                [nodeId for nodeId in workingIds if nodes[nodeId].status != "evicted"]
            ) > self.thoughtConfig.workingMemoryCapacity:
                evictable = [
                    nodes[nodeId]
                    for nodeId in workingIds
                    if nodes[nodeId].status != "evicted" and nodeId != focus.elementId
                ]
                if not evictable:
                    break
                oldest = min(
                    evictable,
                    key=lambda node: (node.lastActivatedStep, node.position),
                )
                oldest.status = "evicted"

            signature = tuple(
                sorted(
                    nodes[nodeId].unitId
                    for nodeId in workingIds
                    if nodes[nodeId].status != "evicted"
                )
            )
            remainingUnexplored = any(
                not nodes[nodeId].explored and nodes[nodeId].status != "evicted"
                for nodeId in workingIds
            )
            if signature == previousSignature and not remainingUnexplored:
                stableCount += 1
            else:
                stableCount = 0
            previousSignature = signature
            steps.append(
                {
                    "step": step,
                    "focus_element_id": focus.elementId,
                    "focus_unit_id": focus.unitId,
                    "added_element_ids": addedIds,
                    "active_signature": list(signature),
                }
            )
            if (
                stableCount >= self.thoughtConfig.stableRepeatLimit
                and not remainingUnexplored
            ):
                break

        leaves = [
            node
            for node in nodes.values()
            if not any(child.parentElementId == node.elementId for child in nodes.values())
        ]
        if not leaves:
            leaves = [root]

        rejectedIds = {
            node.elementId
            for node in nodes.values()
            if node.status == "candidate"
        }
        eligibleLeaves = [leaf for leaf in leaves if leaf.elementId not in rejectedIds]
        if not eligibleLeaves:
            eligibleLeaves = [root]

        def pathRank(leaf: ThoughtNode) -> tuple[int, int, int, float, int]:
            path = self._nodePath(leaf, nodes)
            observedTotal = sum(node.observedDensity for node in path)
            thoughtTotal = sum(node.thoughtDensity for node in path)
            return (
                observedTotal + thoughtTotal,
                observedTotal,
                thoughtTotal,
                sum(node.opacity for node in path),
                len(path),
            )

        bestLeaf = max(eligibleLeaves, key=pathRank)
        conclusionPath = self._nodePath(bestLeaf, nodes)
        conclusionIds = {node.elementId for node in conclusionPath}
        for node in nodes.values():
            if node.elementId in conclusionIds:
                node.status = "conclusion"
            elif node.status == "candidate":
                node.status = "rejected"
            elif node.status != "evicted":
                node.status = "alternative"

        self._persistThought(thoughtId, sourceSentenceId, nodes)
        edges = [
            {
                "source_element_id": node.parentElementId,
                "target_element_id": node.elementId,
                "status": node.status,
            }
            for node in nodes.values()
            if node.parentElementId is not None
        ]
        return {
            "thought_id": thoughtId,
            "nodes": [
                {
                    "element_id": node.elementId,
                    "unit_id": node.unitId,
                    "content": self.get_unit_content(node.unitId),
                    "parent_element_id": node.parentElementId,
                    "position": node.position,
                    "status": node.status,
                    "observed_density": node.observedDensity,
                    "thought_density": node.thoughtDensity,
                    "opacity": node.opacity,
                }
                for node in nodes.values()
            ],
            "edges": edges,
            "conclusion_unit_ids": [node.unitId for node in conclusionPath],
            "steps": steps,
        }

    def _relatedUnitsAtSameDepth(self, unitId: str, limit: int = 500) -> set[str]:
        targetDepth = self.get_unit_depth(unitId)
        ancestorIds: set[str] = set()
        frontier = {unitId}
        while frontier and len(ancestorIds) < limit:
            placeholders = ",".join("?" for _ in frontier)
            rows = self.conn.execute(
                f"""
                SELECT DISTINCT parent_unit_id
                FROM compositions
                WHERE child_unit_id IN ({placeholders})
                """,
                list(frontier),
            ).fetchall()
            nextFrontier = {
                str(row["parent_unit_id"])
                for row in rows
                if str(row["parent_unit_id"]) not in ancestorIds
            }
            ancestorIds.update(nextFrontier)
            frontier = nextFrontier

        related = {unitId}
        frontier = set(ancestorIds)
        visited = set(frontier)
        while frontier and len(visited) < limit:
            placeholders = ",".join("?" for _ in frontier)
            rows = self.conn.execute(
                f"""
                SELECT c.child_unit_id, u.depth
                FROM compositions AS c
                JOIN units AS u ON u.unit_id = c.child_unit_id
                WHERE c.parent_unit_id IN ({placeholders})
                """,
                list(frontier),
            ).fetchall()
            nextFrontier: set[str] = set()
            for row in rows:
                childId = str(row["child_unit_id"])
                childDepth = int(row["depth"])
                if childDepth == targetDepth:
                    related.add(childId)
                elif childDepth > targetDepth and childId not in visited:
                    nextFrontier.add(childId)
            visited.update(nextFrontier)
            frontier = nextFrontier
        return related

    def _loadLayer(self, sentenceId: str, passId: int) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT unit_id
            FROM observed_layer_units
            WHERE sentence_id = ? AND pass_id = ?
            ORDER BY position
            """,
            (sentenceId, passId),
        ).fetchall()
        return [str(row["unit_id"]) for row in rows]

    def express(
        self,
        thoughtResult: dict[str, Any],
        *,
        excludeSentenceId: str | None = None,
    ) -> dict[str, Any]:
        """Find a Unit-position template and replace all mapped conclusion slots."""
        conclusionIds = list(dict.fromkeys(thoughtResult.get("conclusion_unit_ids", [])))
        if not conclusionIds:
            return {
                "base_sentence_id": None,
                "base_pass_id": None,
                "unit_ids": [],
                "contents": [],
                "slot_mapping": {},
                "text": "",
            }

        relatedByConclusion = {
            unitId: self._relatedUnitsAtSameDepth(unitId)
            for unitId in conclusionIds
        }
        allRelated = set().union(*relatedByConclusion.values())
        placeholders = ",".join("?" for _ in allRelated)
        params: list[Any] = list(allRelated)
        excludeFilter = ""
        if excludeSentenceId is not None:
            excludeFilter = "AND sentence_id != ?"
            params.append(excludeSentenceId)
        layerRows = self.conn.execute(
            f"""
            SELECT DISTINCT sentence_id, pass_id
            FROM observed_layer_units
            WHERE unit_id IN ({placeholders})
              {excludeFilter}
            """,
            params,
        ).fetchall()

        layers: dict[tuple[str, int], list[str]] = {}
        for row in layerRows:
            key = (str(row["sentence_id"]), int(row["pass_id"]))
            unitIds = self._loadLayer(*key)
            if unitIds:
                layers[key] = unitIds

        if not layers:
            contents = [self.get_unit_content(unitId) for unitId in conclusionIds]
            return {
                "base_sentence_id": None,
                "base_pass_id": None,
                "unit_ids": conclusionIds,
                "contents": contents,
                "slot_mapping": {},
                "text": "".join(contents),
            }

        groups: dict[int, list[tuple[tuple[str, int], list[str]]]] = defaultdict(list)
        for key, unitIds in layers.items():
            groups[len(unitIds)].append((key, unitIds))

        bestChoice: dict[str, Any] | None = None
        for layerLength, group in groups.items():
            positionCounts = [Counter() for _ in range(layerLength)]
            for _, unitIds in group:
                for position, unitId in enumerate(unitIds):
                    positionCounts[position][unitId] += 1

            slotMapping: dict[str, int] = {}
            usedPositions: set[int] = set()
            slotTotal = 0
            for conclusionId in conclusionIds:
                relatedIds = relatedByConclusion[conclusionId]
                rankedPositions = []
                for position, counts in enumerate(positionCounts):
                    density = sum(counts[unitId] for unitId in relatedIds)
                    rankedPositions.append((density, position))
                rankedPositions.sort(reverse=True)
                selected = next(
                    (
                        (density, position)
                        for density, position in rankedPositions
                        if density > 0 and position not in usedPositions
                    ),
                    None,
                )
                if selected is None:
                    continue
                density, position = selected
                slotMapping[conclusionId] = position
                usedPositions.add(position)
                slotTotal += density

            for key, unitIds in group:
                skeletonDensity = sum(
                    positionCounts[position][unitId]
                    for position, unitId in enumerate(unitIds)
                    if position not in usedPositions
                )
                mappedCount = sum(
                    1
                    for conclusionId, position in slotMapping.items()
                    if unitIds[position] in relatedByConclusion[conclusionId]
                )
                rank = (
                    len(slotMapping),
                    slotTotal,
                    mappedCount,
                    skeletonDensity,
                    layerLength,
                )
                if bestChoice is None or rank > bestChoice["rank"]:
                    bestChoice = {
                        "rank": rank,
                        "key": key,
                        "unit_ids": list(unitIds),
                        "slot_mapping": dict(slotMapping),
                        "position_counts": positionCounts,
                    }

        if bestChoice is None:
            contents = [self.get_unit_content(unitId) for unitId in conclusionIds]
            return {
                "base_sentence_id": None,
                "base_pass_id": None,
                "unit_ids": conclusionIds,
                "contents": contents,
                "slot_mapping": {},
                "text": "".join(contents),
            }

        outputIds = list(bestChoice["unit_ids"])
        for conclusionId, position in bestChoice["slot_mapping"].items():
            outputIds[position] = conclusionId
        contents = [self.get_unit_content(unitId) for unitId in outputIds]
        baseSentenceId, basePassId = bestChoice["key"]
        return {
            "base_sentence_id": baseSentenceId,
            "base_pass_id": basePassId,
            "unit_ids": outputIds,
            "contents": contents,
            "slot_mapping": bestChoice["slot_mapping"],
            "text": "".join(contents),
        }

    def respond(self, expressionResult: dict[str, Any]) -> str:
        return str(expressionResult.get("text", ""))

    def process_sentence(self, raw_text: str) -> dict[str, Any]:
        normalized = str(raw_text).strip()
        if not normalized:
            return {
                "sentence_id": "",
                "raw_text": "",
                "pass_results": [],
                "word_segments": [],
                "thought_results": [],
                "recall_result": {"active_unit_ids": [], "candidates": []},
                "thought_result": {
                    "thought_id": "",
                    "nodes": [],
                    "edges": [],
                    "conclusion_unit_ids": [],
                    "steps": [],
                },
                "expression_result": {
                    "base_sentence_id": None,
                    "base_pass_id": None,
                    "unit_ids": [],
                    "contents": [],
                    "slot_mapping": {},
                    "text": "",
                },
                "response": "",
            }

        sentence_id = f"sentence-{uuid.uuid4().hex[:8]}"
        current_unit_ids = [self.get_or_create_unit(ch) for ch in normalized]

        pass_results: list[dict[str, Any]] = []
        pass_id = 0

        while pass_id < 100:
            projected_ids = self._project_existing_units(current_unit_ids)
            segmented_ids, densities = self._discover_from_density(
                projected_ids, sentence_id
            )

            self._record_cell_elements(current_unit_ids, pass_id, sentence_id)

            contents = [self.get_unit_content(uid) for uid in segmented_ids]
            depths = [self.get_unit_depth(uid) for uid in segmented_ids]
            pass_results.append(
                {
                    "pass_id": pass_id,
                    "input_count": len(current_unit_ids),
                    "output_count": len(segmented_ids),
                    "unit_ids": segmented_ids,
                    "contents": contents,
                    "depths": depths,
                    "top_view_densities": densities,
                }
            )

            if len(segmented_ids) == 1:
                break
            if segmented_ids == current_unit_ids:
                break

            current_unit_ids = segmented_ids
            pass_id += 1

        if pass_results:
            finalUnitIds = list(pass_results[-1]["unit_ids"])
            if finalUnitIds != current_unit_ids:
                self._record_observed_layer(
                    finalUnitIds,
                    pass_id + 1,
                    sentence_id,
                )

        word_segments = pass_results[0]["contents"] if pass_results else []
        activeUnitIds = list(
            dict.fromkeys(
                unitId
                for passResult in pass_results
                for unitId in passResult["unit_ids"]
                if self.get_unit_depth(unitId) >= 2
            )
        )
        if not activeUnitIds and pass_results:
            activeUnitIds = list(pass_results[-1]["unit_ids"])
        recallResult = self.recall(
            activeUnitIds,
            excludeSentenceId=sentence_id,
        )
        thoughtResult = self.think(
            recallResult,
            sourceSentenceId=sentence_id,
        )
        expressionResult = self.express(
            thoughtResult,
            excludeSentenceId=sentence_id,
        )
        response = self.respond(expressionResult)
        thought_results = [
            str(candidate["content"])
            for candidate in recallResult["candidates"]
        ]

        return {
            "sentence_id": sentence_id,
            "raw_text": normalized,
            "word_segments": word_segments,
            "pass_results": pass_results,
            "thought_results": thought_results,
            "recall_result": recallResult,
            "thought_result": thoughtResult,
            "expression_result": expressionResult,
            "response": response,
        }

    def _think_side_view(
        self,
        current_sentence_id: str,
        pass_results: list[dict[str, Any]],
    ) -> list[str]:
        active_ids: set[str] = set()
        for pass_result in pass_results:
            for unit_id, depth in zip(
                pass_result["unit_ids"], pass_result["depths"], strict=False
            ):
                if depth >= 2:
                    active_ids.add(unit_id)

        if not active_ids:
            return []

        placeholders = ",".join("?" for _ in active_ids)
        ids = list(active_ids)
        rows = self.conn.execute(
            f"""
            SELECT DISTINCT sentence_id
            FROM cell_elements
            WHERE (source_unit_id IN ({placeholders}) OR next_unit_id IN ({placeholders}))
              AND link_depth >= 2
              AND sentence_id != ?
            """,
            ids + ids + [current_sentence_id],
        ).fetchall()

        intersecting = [str(row["sentence_id"]) for row in rows]
        if not intersecting:
            return []

        sentence_placeholders = ",".join("?" for _ in intersecting)
        assoc_rows = self.conn.execute(
            f"""
            SELECT u.content, SUM(ce.opacity) AS total_weight
            FROM cell_elements ce
            JOIN units u ON ce.next_unit_id = u.unit_id
            WHERE ce.sentence_id IN ({sentence_placeholders})
              AND ce.link_depth >= 2
              AND u.depth >= 2
            GROUP BY u.content
            ORDER BY total_weight DESC
            """,
            intersecting,
        ).fetchall()

        current_contents: set[str] = set()
        for pass_result in pass_results:
            current_contents.update(pass_result["contents"])

        raw: list[tuple[str, float]] = []
        seen: set[str] = set()
        for row in assoc_rows:
            word = str(row["content"]).strip()
            if not word or word in current_contents or word in seen:
                continue
            seen.add(word)
            raw.append((word, float(row["total_weight"])))

        if not raw:
            return []

        particle_set = {
            "은", "는", "이", "가", "을", "를", "의", "에", "로", "으로",
            "도", "과", "와", "에서", "에게", "하며", "하고", "이며", "이다",
            "있다", "없다", "다.", "다", "한", "인", "은,",
        }
        max_weight = raw[0][1]

        filtered: list[str] = []
        for word, weight in raw:
            if word in particle_set:
                continue
            if len(word) == 1 and word in "은는이가을를의에도과와로한인":
                continue
            if weight >= max_weight * 0.85 and any(
                word.endswith(p)
                for p in particle_set
                if len(word) <= len(p) + 1
            ):
                continue
            filtered.append(word)

        return filtered[:20]
