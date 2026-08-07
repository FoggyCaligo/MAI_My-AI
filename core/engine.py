from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Database, get_default_db_path

PathState = tuple[str, int, float]  # (sentence_id, position, opacity)


@dataclass(slots=True)
class UnitChoice:
    segment_units: list[str]  # list of unit_ids
    next_index: int
    support_score: float
    supported_length: int
    piece_count: int


class CognitiveEngine:
    """Multi-depth cognitive engine using cellophane layering, opacity feedback, and side-view thinking."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db = Database(db_path)
        self.conn = self.db.conn

    def close(self) -> None:
        self.db.close()

    def get_or_create_unit(self, depth: int, content: str) -> str:
        """Find existing unit or create a new unit at specified depth."""
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
        self.conn.commit()
        return unit_id

    def get_unit_content(self, unit_id: str) -> str:
        row = self.conn.execute(
            "SELECT content FROM units WHERE unit_id = ?", (unit_id,)
        ).fetchone()
        return str(row["content"]) if row else unit_id

    def apply_feedback(self, sentence_id: str, score: float) -> float:
        """
        Applies user feedback score (0 to 100) subtly to thought/concept layers (Depth >= 2).
        - 50 points: No change (multiplier = 1.0)
        - 100 points: Subtle increase (+25%, multiplier = 1.25)
        - 0 points: Subtle decrease (-25%, multiplier = 0.75)
        Depth 0 (character) and Depth 1 (word segmentation) layers remain untouched.
        """
        clamped_score = max(0.0, min(100.0, float(score)))
        # Subtle multiplier range: 50 -> 1.0, 100 -> 1.25, 0 -> 0.75
        multiplier = 1.0 + (clamped_score - 50.0) * 0.005

        rows = self.conn.execute(
            "SELECT opacity FROM unit_links WHERE sentence_id = ? AND depth >= 2", (sentence_id,)
        ).fetchall()

        if not rows:
            return 1.0

        current_avg_opacity = sum(float(r["opacity"]) for r in rows) / len(rows)
        new_avg_opacity = max(0.01, min(10.0, current_avg_opacity * multiplier))

        self.conn.execute(
            """
            UPDATE unit_links
            SET opacity = MAX(0.01, MIN(10.0, opacity * ?))
            WHERE sentence_id = ? AND depth >= 2
            """,
            (multiplier, sentence_id),
        )
        self.conn.commit()
        return new_avg_opacity

    def process_sentence(self, raw_text: str) -> dict[str, Any]:
        normalized = str(raw_text).strip()
        if not normalized:
            return {
                "sentence_id": "",
                "raw_text": "",
                "depth_results": [],
                "word_segments": [],
                "thought_results": [],
            }

        sentence_id = f"sentence-{uuid.uuid4().hex[:8]}"

        depth_results: list[dict[str, Any]] = []
        
        # Start at Depth 0: Characters
        char_units = [self.get_or_create_unit(0, ch) for ch in list(normalized)]
        current_units = char_units

        depth = 0
        max_depth_limit = 100  # Safety circuit-breaker

        while depth < max_depth_limit:
            # 1. Segment current units using cellophane overlap at this depth
            segmented_unit_ids = self._segment_units_at_depth(current_units, depth)

            # 2. Ingest transition links for current level
            self._ingest_unit_links(current_units, depth, sentence_id)

            # Map unit_ids to readable content
            segmented_contents = [self.get_unit_content(uid) for uid in segmented_unit_ids]

            depth_results.append({
                "depth": depth,
                "input_count": len(current_units),
                "output_count": len(segmented_unit_ids),
                "unit_ids": segmented_unit_ids,
                "contents": segmented_contents,
            })

            # Check Termination Condition:
            # 1. "입력 문장 전체가 하나의 덩어리로 잡힐 때까지"
            if len(segmented_unit_ids) == 1:
                break

            # 2. 더 이상 덩어리로 줄어들지 않고 동일할 경우 termination
            if segmented_unit_ids == current_units:
                break

            # Prepare higher depth units for next pass
            next_units = []
            for content in segmented_contents:
                next_uid = self.get_or_create_unit(depth + 1, content)
                next_units.append(next_uid)
            current_units = next_units

            depth += 1

        # Word segmentation result is from Depth 0 -> Depth 1
        word_segments = depth_results[0]["contents"] if depth_results else []

        # Side-View Thinking: Executed ONLY AFTER vertical layering terminates!
        thought_results = self._think_side_view(sentence_id, depth_results)

        return {
            "sentence_id": sentence_id,
            "raw_text": normalized,
            "word_segments": word_segments,
            "depth_results": depth_results,
            "thought_results": thought_results,
        }

    def _segment_units_at_depth(self, unit_ids: list[str], depth: int) -> list[str]:
        length = len(unit_ids)
        if length <= 1:
            return list(unit_ids)

        step_matches = self._build_step_matches(unit_ids, depth)
        best: list[UnitChoice | None] = [None] * (length + 1)
        best[length] = UnitChoice([], length, 0.0, 0, 0)

        for start in range(length - 1, -1, -1):
            fallback = best[start + 1]
            assert fallback is not None

            # Single unit fallback
            best_choice = UnitChoice(
                segment_units=[unit_ids[start]],
                next_index=start + 1,
                support_score=fallback.support_score,
                supported_length=fallback.supported_length,
                piece_count=fallback.piece_count + 1,
            )

            for end in range(start + 2, length + 1):
                support_score = self._support_score(step_matches, start, end)
                if support_score == 0.0:
                    continue

                remainder = best[end]
                assert remainder is not None
                span_length = end - start

                # Create merged content for span
                merged_content = "".join(self.get_unit_content(uid) for uid in unit_ids[start:end])
                merged_unit_id = self.get_or_create_unit(depth + 1, merged_content)

                candidate = UnitChoice(
                    segment_units=[merged_unit_id],
                    next_index=end,
                    support_score=remainder.support_score + (support_score * span_length),
                    supported_length=remainder.supported_length + span_length,
                    piece_count=remainder.piece_count + 1,
                )

                if self._is_better(candidate, best_choice):
                    best_choice = candidate

            best[start] = best_choice

        result_units: list[str] = []
        index = 0
        while index < length:
            choice = best[index]
            assert choice is not None
            result_units.extend(choice.segment_units)
            index = choice.next_index
        return result_units

    def _build_step_matches(self, unit_ids: list[str], depth: int) -> list[set[PathState]]:
        step_matches: list[set[PathState]] = []
        for i in range(len(unit_ids) - 1):
            rows = self.conn.execute(
                """
                SELECT sentence_id, position, opacity
                FROM unit_links
                WHERE source_unit_id = ? AND target_unit_id = ? AND depth = ?
                """,
                (unit_ids[i], unit_ids[i + 1], depth),
            ).fetchall()
            step_matches.append({
                (str(row["sentence_id"]), int(row["position"]), float(row["opacity"]))
                for row in rows
            })
        return step_matches

    def _support_score(self, step_matches: list[set[PathState]], start: int, end: int) -> float:
        if end - start < 2:
            return 0.0

        survivors = set(step_matches[start])
        if not survivors:
            return 0.0

        for step_index in range(start + 1, end - 1):
            next_matches = step_matches[step_index]
            if not next_matches:
                return 0.0

            advanced: set[PathState] = set()
            for sentence_id, position, opacity in survivors:
                # Find matching target state in next step
                matching_next = [
                    (s_id, pos, op)
                    for s_id, pos, op in next_matches
                    if s_id == sentence_id and pos == position + 1
                ]
                for next_state in matching_next:
                    advanced.add(next_state)

            survivors = advanced
            if not survivors:
                return 0.0

        # Sum opacity across all surviving path layers
        return sum(opacity for _, _, opacity in survivors)

    def _is_better(self, candidate: UnitChoice, current: UnitChoice) -> bool:
        candidate_key = (
            candidate.support_score,
            candidate.supported_length,
            -candidate.piece_count,
        )
        current_key = (
            current.support_score,
            current.supported_length,
            -current.piece_count,
        )
        return candidate_key > current_key

    def _ingest_unit_links(self, unit_ids: list[str], depth: int, sentence_id: str) -> None:
        if len(unit_ids) < 2:
            return

        self.conn.executemany(
            """
            INSERT INTO unit_links (source_unit_id, target_unit_id, sentence_id, position, depth)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                (unit_ids[i], unit_ids[i + 1], sentence_id, i, depth)
                for i in range(len(unit_ids) - 1)
            ),
        )
        self.conn.commit()

    def _think_side_view(self, current_sentence_id: str, depth_results: list[dict[str, Any]]) -> list[str]:
        """
        Side-View Cross-Section Thinking:
        Collects unit_ids active in the current sentence from Depth 2 and above,
        finds intersecting sentence_ids, and extracts associative units.
        Filters out particle plateau groups (과도하게 튀면서 엇비슷한 고빈도 조사 그룹)
        and returns the Top 20 most prominent conceptual units.
        """
        active_unit_ids = set()
        for dr in depth_results:
            if dr["depth"] >= 2:
                active_unit_ids.update(dr["unit_ids"])

        if not active_unit_ids:
            return []

        placeholders = ",".join("?" for _ in active_unit_ids)
        query = f"""
            SELECT DISTINCT sentence_id
            FROM unit_links
            WHERE (source_unit_id IN ({placeholders}) OR target_unit_id IN ({placeholders}))
              AND depth >= 2
              AND sentence_id != ?
        """
        params = list(active_unit_ids) + list(active_unit_ids) + [current_sentence_id]
        rows = self.conn.execute(query, params).fetchall()

        intersecting_sentence_ids = [str(r["sentence_id"]) for r in rows]
        if not intersecting_sentence_ids:
            return []

        # Fetch candidate units ordered by total opacity weight
        sent_placeholders = ",".join("?" for _ in intersecting_sentence_ids)
        unit_query = f"""
            SELECT u.content, SUM(ul.opacity) as total_weight, COUNT(ul.id) as freq
            FROM unit_links ul
            JOIN units u ON ul.target_unit_id = u.unit_id
            WHERE ul.sentence_id IN ({sent_placeholders})
              AND ul.depth >= 2
              AND u.depth >= 2
            GROUP BY u.content
            ORDER BY total_weight DESC
        """
        assoc_rows = self.conn.execute(unit_query, intersecting_sentence_ids).fetchall()

        current_contents = set()
        for dr in depth_results:
            current_contents.update(dr["contents"])

        raw_candidates: list[tuple[str, float]] = []
        for r in assoc_rows:
            word = str(r["content"]).strip()
            weight = float(r["total_weight"])
            if word and word not in current_contents and word not in [w for w, _ in raw_candidates]:
                raw_candidates.append((word, weight))

        if not raw_candidates:
            return []

        # Korean particles / functional suffixes that form high-frequency plateau groups
        PARTICLE_SET = {
            "은", "는", "이", "가", "을", "를", "의", "에", "로", "으로",
            "도", "과", "와", "에서", "에게", "하며", "하고", "이며", "이다",
            "있다", "없다", "다.", "다", "한", "인", "은,"
        }

        weights = [w for _, w in raw_candidates]
        max_w = weights[0] if weights else 1.0

        filtered_candidates: list[str] = []
        for word, weight in raw_candidates:
            # 1. Direct match with particle set
            if word in PARTICLE_SET:
                continue
            # 2. Single-character functional particles
            if len(word) == 1 and word in "은는이가을를의에도과와로한인":
                continue
            # 3. High-frequency plateau check: if item is in top 90% weight plateau and particle-heavy
            if weight >= max_w * 0.85 and any(word.endswith(p) for p in PARTICLE_SET if len(word) <= len(p) + 1):
                continue

            filtered_candidates.append(word)

        # Return Top 20 prominent concept units
        return filtered_candidates[:20]
