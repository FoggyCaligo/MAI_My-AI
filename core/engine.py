from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Database


CellKey = tuple[str, int]  # (sentence_id, position)
CellIndex = dict[str, dict[str, dict[CellKey, float]]]


@dataclass(slots=True)
class UnitChoice:
    segment_units: list[str]
    next_index: int
    overlap_weight: float
    overlap_count: int
    supported_length: int
    piece_count: int


def _compute_merged_depth(child_unit_depths: list[int]) -> int:
    return max(child_unit_depths) + 1


class CognitiveEngine:
    """
    Recursive MAI engine backed by a cellophane-style cell index.

    Storage is persistent in SQLite, but overlap calculation is performed in memory:

        source Unit cell
          -> next Unit address
          -> sentence_id
          -> position
          -> opacity

    pass_id is observation metadata only. Unit identity and overlap matching do not
    require the same pass_id.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db = Database(db_path)
        self.conn = self.db.conn

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------ #
    # Unit helpers
    # ------------------------------------------------------------------ #

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

    def get_or_create_unit(
        self,
        content: str,
        child_unit_ids: list[str] | None = None,
    ) -> str:
        if not child_unit_ids:
            depth = 0
        else:
            depth = _compute_merged_depth(
                [self.get_unit_depth(uid) for uid in child_unit_ids]
            )

        row = self.conn.execute(
            "SELECT unit_id FROM units WHERE depth = ? AND content = ?",
            (depth, content),
        ).fetchone()
        if row:
            return str(row["unit_id"])

        unit_id = f"unit-d{depth}-{uuid.uuid4().hex[:8]}"
        self.conn.execute(
            """
            INSERT INTO units (unit_id, depth, content, support_count)
            VALUES (?, ?, ?, 1)
            """,
            (unit_id, depth, content),
        )

        if child_unit_ids:
            self.conn.executemany(
                """
                INSERT INTO compositions
                    (parent_unit_id, child_unit_id, position)
                VALUES (?, ?, ?)
                """,
                [(unit_id, child_id, pos) for pos, child_id in enumerate(child_unit_ids)],
            )

        self.conn.commit()
        return unit_id

    # ------------------------------------------------------------------ #
    # Feedback
    # ------------------------------------------------------------------ #

    def apply_feedback(self, sentence_id: str, score: float) -> float:
        """
        Feedback changes the opacity of thought-level cell elements.

        50 -> unchanged
        100 -> x1.25
        0 -> x0.75
        """
        clamped = max(0.0, min(100.0, float(score)))
        multiplier = 1.0 + (clamped - 50.0) * 0.005

        rows = self.conn.execute(
            """
            SELECT opacity
            FROM cell_elements
            WHERE sentence_id = ? AND cell_depth >= 2
            """,
            (sentence_id,),
        ).fetchall()
        if not rows:
            return 1.0

        current_avg = sum(float(row["opacity"]) for row in rows) / len(rows)
        new_avg = max(0.01, min(10.0, current_avg * multiplier))

        self.conn.execute(
            """
            UPDATE cell_elements
            SET opacity = MAX(0.01, MIN(10.0, opacity * ?))
            WHERE sentence_id = ? AND cell_depth >= 2
            """,
            (multiplier, sentence_id),
        )
        self.conn.commit()
        return new_avg

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #

    def process_sentence(self, raw_text: str) -> dict[str, Any]:
        normalized = str(raw_text).strip()
        if not normalized:
            return {
                "sentence_id": "",
                "raw_text": "",
                "pass_results": [],
                "word_segments": [],
                "thought_results": [],
            }

        sentence_id = f"sentence-{uuid.uuid4().hex[:8]}"
        current_unit_ids = [
            self.get_or_create_unit(ch) for ch in list(normalized)
        ]

        pass_results: list[dict[str, Any]] = []
        max_pass_limit = 100

        for pass_id in range(max_pass_limit):
            # One DB read loads the relevant vertical cells. The current sentence is
            # excluded so it cannot become its own evidence while being processed.
            cell_index = self._load_cell_index(
                set(current_unit_ids),
                exclude_sentence_id=sentence_id,
            )

            segmented_ids = self._segment_units(current_unit_ids, cell_index)

            # Store this observation only after segmentation.
            self._ingest_cell_elements(current_unit_ids, pass_id, sentence_id)

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
                }
            )

            if len(segmented_ids) == 1:
                break
            if segmented_ids == current_unit_ids:
                break

            current_unit_ids = segmented_ids

        word_segments = pass_results[0]["contents"] if pass_results else []
        thought_results = self._think_side_view(sentence_id, pass_results)

        return {
            "sentence_id": sentence_id,
            "raw_text": normalized,
            "word_segments": word_segments,
            "pass_results": pass_results,
            "thought_results": thought_results,
        }

    # ------------------------------------------------------------------ #
    # Cellophane top-view
    # ------------------------------------------------------------------ #

    def _load_cell_index(
        self,
        source_unit_ids: set[str],
        *,
        exclude_sentence_id: str | None = None,
    ) -> CellIndex:
        """
        Load all relevant cells in one query.

        Multiple observations of the same edge at the same sentence position but at
        different recursive passes represent the same sentence layer for top-view
        matching, so they are collapsed with MAX(opacity).
        """
        if not source_unit_ids:
            return {}

        placeholders = ",".join("?" for _ in source_unit_ids)
        params: list[Any] = list(source_unit_ids)
        exclude_sql = ""
        if exclude_sentence_id is not None:
            exclude_sql = "AND sentence_id != ?"
            params.append(exclude_sentence_id)

        rows = self.conn.execute(
            f"""
            SELECT
                source_unit_id,
                next_unit_id,
                sentence_id,
                position,
                MAX(opacity) AS opacity
            FROM cell_elements
            WHERE source_unit_id IN ({placeholders})
              {exclude_sql}
            GROUP BY
                source_unit_id,
                next_unit_id,
                sentence_id,
                position
            """,
            params,
        ).fetchall()

        index: CellIndex = {}
        for row in rows:
            source_id = str(row["source_unit_id"])
            next_id = str(row["next_unit_id"])
            key = (str(row["sentence_id"]), int(row["position"]))
            opacity = float(row["opacity"])
            index.setdefault(source_id, {}).setdefault(next_id, {})[key] = opacity
        return index

    def _collect_span_overlaps(
        self,
        unit_ids: list[str],
        cell_index: CellIndex,
    ) -> dict[tuple[int, int], tuple[float, int]]:
        """
        Overlay the current sequence on the vertical sentence layers.

        For every start position, paths survive only while:
          - the next-unit address matches, and
          - the same sentence_id continues at position + 1.

        The result for (start, end) is:
          (weighted visible-layer count, raw surviving-layer count)
        """
        overlaps: dict[tuple[int, int], tuple[float, int]] = {}
        length = len(unit_ids)

        for start in range(length - 1):
            first_edge = (
                cell_index
                .get(unit_ids[start], {})
                .get(unit_ids[start + 1], {})
            )
            if not first_edge:
                continue

            active: dict[CellKey, float] = dict(first_edge)
            overlaps[(start, start + 2)] = (
                sum(active.values()),
                len(active),
            )

            for edge_pos in range(start + 1, length - 1):
                next_edge = (
                    cell_index
                    .get(unit_ids[edge_pos], {})
                    .get(unit_ids[edge_pos + 1], {})
                )
                if not next_edge:
                    break

                advanced: dict[CellKey, float] = {}
                for (sentence_id, previous_position), path_opacity in active.items():
                    next_key = (sentence_id, previous_position + 1)
                    next_opacity = next_edge.get(next_key)
                    if next_opacity is None:
                        continue
                    advanced[next_key] = min(path_opacity, next_opacity)

                if not advanced:
                    break

                active = advanced
                overlaps[(start, edge_pos + 2)] = (
                    sum(active.values()),
                    len(active),
                )

        return overlaps

    def _segment_units(
        self,
        unit_ids: list[str],
        cell_index: CellIndex,
    ) -> list[str]:
        """
        Choose a segmentation from the visible top-view overlap.

        With opacity 1.0, overlap_weight is literally the number of surviving
        sentence layers. For candidate comparison we multiply that layer count by
        the number of traversed address cells in the span.
        """
        length = len(unit_ids)
        if length <= 1:
            return list(unit_ids)

        overlaps = self._collect_span_overlaps(unit_ids, cell_index)

        best: list[UnitChoice | None] = [None] * (length + 1)
        best[length] = UnitChoice([], length, 0.0, 0, 0, 0)

        for start in range(length - 1, -1, -1):
            fallback = best[start + 1]
            assert fallback is not None

            best_choice = UnitChoice(
                segment_units=[unit_ids[start]],
                next_index=start + 1,
                overlap_weight=fallback.overlap_weight,
                overlap_count=fallback.overlap_count,
                supported_length=fallback.supported_length,
                piece_count=fallback.piece_count + 1,
            )

            for end in range(start + 2, length + 1):
                overlap = overlaps.get((start, end))
                if overlap is None:
                    continue

                visible_weight, visible_count = overlap
                remainder = best[end]
                assert remainder is not None

                child_ids = unit_ids[start:end]
                merged_content = "".join(
                    self.get_unit_content(uid) for uid in child_ids
                )
                merged_id = self.get_or_create_unit(merged_content, child_ids)

                edge_count = end - start - 1
                candidate = UnitChoice(
                    segment_units=[merged_id],
                    next_index=end,
                    # A surviving sentence layer occupies one address-filled cell
                    # for every traversed edge in the span.
                    overlap_weight=(
                        remainder.overlap_weight
                        + visible_weight * edge_count
                    ),
                    overlap_count=(
                        remainder.overlap_count
                        + visible_count * edge_count
                    ),
                    supported_length=remainder.supported_length + (end - start),
                    piece_count=remainder.piece_count + 1,
                )

                if self._is_better(candidate, best_choice):
                    best_choice = candidate

            best[start] = best_choice

        result: list[str] = []
        index = 0
        while index < length:
            choice = best[index]
            assert choice is not None
            result.extend(choice.segment_units)
            index = choice.next_index
        return result

    @staticmethod
    def _is_better(candidate: UnitChoice, current: UnitChoice) -> bool:
        return (
            candidate.overlap_weight,
            candidate.overlap_count,
            candidate.supported_length,
            -candidate.piece_count,
        ) > (
            current.overlap_weight,
            current.overlap_count,
            current.supported_length,
            -current.piece_count,
        )

    def _ingest_cell_elements(
        self,
        unit_ids: list[str],
        pass_id: int,
        sentence_id: str,
    ) -> None:
        """
        Store one sentence layer.

        next_unit_id is the directly followable "address" inside each cell.
        sentence_id + position reconstruct the observation order.
        """
        if len(unit_ids) < 2:
            return

        rows = []
        for position in range(len(unit_ids) - 1):
            source_id = unit_ids[position]
            next_id = unit_ids[position + 1]
            cell_depth = max(
                self.get_unit_depth(source_id),
                self.get_unit_depth(next_id),
            )
            rows.append(
                (
                    source_id,
                    next_id,
                    sentence_id,
                    position,
                    pass_id,
                    cell_depth,
                )
            )

        self.conn.executemany(
            """
            INSERT OR IGNORE INTO cell_elements
                (
                    source_unit_id,
                    next_unit_id,
                    sentence_id,
                    position,
                    pass_id,
                    cell_depth
                )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # Side-view thinking
    # ------------------------------------------------------------------ #

    def _think_side_view(
        self,
        current_sentence_id: str,
        pass_results: list[dict[str, Any]],
    ) -> list[str]:
        active_ids: set[str] = set()
        for pass_result in pass_results:
            for unit_id, depth in zip(
                pass_result["unit_ids"],
                pass_result["depths"],
            ):
                if depth >= 2:
                    active_ids.add(unit_id)

        if not active_ids:
            return []

        placeholders = ",".join("?" for _ in active_ids)
        id_list = list(active_ids)

        rows = self.conn.execute(
            f"""
            SELECT DISTINCT sentence_id
            FROM cell_elements
            WHERE (
                source_unit_id IN ({placeholders})
                OR next_unit_id IN ({placeholders})
            )
              AND cell_depth >= 2
              AND sentence_id != ?
            """,
            id_list + id_list + [current_sentence_id],
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
              AND ce.cell_depth >= 2
              AND u.depth >= 2
            GROUP BY u.content
            ORDER BY total_weight DESC
            """,
            intersecting,
        ).fetchall()

        current_contents: set[str] = set()
        for pass_result in pass_results:
            current_contents.update(pass_result["contents"])

        particle_set = {
            "은", "는", "이", "가", "을", "를", "의", "에", "로", "으로",
            "도", "과", "와", "에서", "에게", "하며", "하고", "이며", "이다",
            "있다", "없다", "다.", "다", "한", "인", "은,",
        }

        raw: list[tuple[str, float]] = []
        seen: set[str] = set()
        for row in assoc_rows:
            content = str(row["content"]).strip()
            weight = float(row["total_weight"])
            if not content or content in current_contents or content in seen:
                continue
            seen.add(content)
            raw.append((content, weight))

        if not raw:
            return []

        max_weight = raw[0][1]
        filtered: list[str] = []
        for content, weight in raw:
            if content in particle_set:
                continue
            if len(content) == 1 and content in "은는이가을를의에도과와로한인":
                continue
            if weight >= max_weight * 0.85 and any(
                content.endswith(particle)
                for particle in particle_set
                if len(content) <= len(particle) + 1
            ):
                continue
            filtered.append(content)

        return filtered[:20]
