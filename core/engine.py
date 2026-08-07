from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .db import Database, get_default_db_path

# PathState: (sentence_id, position, opacity)
PathState = tuple[str, int, float]


@dataclass(slots=True)
class UnitChoice:
    segment_units: list[str]          # list of unit_ids (may be mixed depths)
    next_index: int
    support_score: float
    supported_length: int
    piece_count: int


def _compute_merged_depth(child_unit_depths: list[int]) -> int:
    """
    new_depth = max(child.depth) + 1
    This is the canonical MAI depth rule.
    """
    return max(child_unit_depths) + 1


class CognitiveEngine:
    """
    Multi-depth cognitive engine.

    Key separation:
      pass_id  — which iteration of the recursive loop (0, 1, 2, …)
      unit.depth — max(child.depth) + 1, independent of pass_id

    Units are never replaced. [눈]d0 and [눈꽃]d1 coexist in the DB.
    A new composition references its actual child unit_ids to compute its own depth.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db = Database(db_path)
        self.conn = self.db.conn

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------ #
    # Unit helpers                                                         #
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

    def get_or_create_unit(self, content: str, child_unit_ids: list[str] | None = None) -> str:
        """
        Find or create a unit whose depth is computed from its children.
        - depth 0 units (characters): child_unit_ids = None or []
        - higher units: depth = max(child.depth) + 1
        Compositions are recorded for every new higher-level unit.
        """
        if not child_unit_ids:
            depth = 0
        else:
            child_depths = [self.get_unit_depth(uid) for uid in child_unit_ids]
            depth = _compute_merged_depth(child_depths)

        row = self.conn.execute(
            "SELECT unit_id FROM units WHERE depth = ? AND content = ?",
            (depth, content),
        ).fetchone()

        if row:
            return str(row["unit_id"])

        unit_id = f"unit-d{depth}-{uuid.uuid4().hex[:8]}"
        self.conn.execute(
            "INSERT INTO units (unit_id, depth, content, support_count) VALUES (?, ?, ?, 1)",
            (unit_id, depth, content),
        )

        if child_unit_ids:
            self.conn.executemany(
                "INSERT INTO compositions (parent_unit_id, child_unit_id, position) VALUES (?, ?, ?)",
                [(unit_id, cid, pos) for pos, cid in enumerate(child_unit_ids)],
            )

        self.conn.commit()
        return unit_id

    # ------------------------------------------------------------------ #
    # Feedback                                                             #
    # ------------------------------------------------------------------ #

    def apply_feedback(self, sentence_id: str, score: float) -> float:
        """
        Applies user feedback score (0–100) subtly to thought layers (link_depth >= 2).
          50  → multiplier = 1.0  (no change)
          100 → multiplier = 1.25 (+25 %)
          0   → multiplier = 0.75 (−25 %)
        Depth-0 and depth-1 links remain untouched.
        """
        clamped = max(0.0, min(100.0, float(score)))
        multiplier = 1.0 + (clamped - 50.0) * 0.005

        rows = self.conn.execute(
            "SELECT opacity FROM unit_links WHERE sentence_id = ? AND link_depth >= 2",
            (sentence_id,),
        ).fetchall()

        if not rows:
            return 1.0

        avg = sum(float(r["opacity"]) for r in rows) / len(rows)
        new_avg = max(0.01, min(10.0, avg * multiplier))

        self.conn.execute(
            """
            UPDATE unit_links
            SET opacity = MAX(0.01, MIN(10.0, opacity * ?))
            WHERE sentence_id = ? AND link_depth >= 2
            """,
            (multiplier, sentence_id),
        )
        self.conn.commit()
        return new_avg

    # ------------------------------------------------------------------ #
    # Main entry point                                                     #
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

        # Pass 0: characters (depth 0 units, mixed-depth allowed from pass 1 onward)
        current_unit_ids: list[str] = [
            self.get_or_create_unit(ch, None) for ch in list(normalized)
        ]

        pass_results: list[dict[str, Any]] = []
        pass_id = 0
        max_pass_limit = 100

        while pass_id < max_pass_limit:
            # 1. Segment — may return units of mixed actual depths
            segmented_ids = self._segment_units(current_unit_ids, pass_id, sentence_id)

            # 2. Ingest unit_links for this pass (using actual unit depths for link_depth)
            self._ingest_unit_links(current_unit_ids, pass_id, sentence_id)

            contents = [self.get_unit_content(uid) for uid in segmented_ids]
            depths   = [self.get_unit_depth(uid)   for uid in segmented_ids]

            pass_results.append({
                "pass_id":      pass_id,
                "input_count":  len(current_unit_ids),
                "output_count": len(segmented_ids),
                "unit_ids":     segmented_ids,
                "contents":     contents,
                "depths":       depths,
            })

            # Termination: whole sentence is one unit
            if len(segmented_ids) == 1:
                break

            # Termination: no further reduction
            if segmented_ids == current_unit_ids:
                break

            current_unit_ids = segmented_ids
            pass_id += 1

        # Word segments come from pass 0 output
        word_segments = pass_results[0]["contents"] if pass_results else []

        # Side-View Thinking (after vertical layering is done)
        thought_results = self._think_side_view(sentence_id, pass_results)

        return {
            "sentence_id":   sentence_id,
            "raw_text":      normalized,
            "word_segments": word_segments,
            "pass_results":  pass_results,
            "thought_results": thought_results,
        }

    # ------------------------------------------------------------------ #
    # Segmentation                                                         #
    # ------------------------------------------------------------------ #

    def _segment_units(
        self,
        unit_ids: list[str],
        pass_id: int,
        sentence_id: str,
    ) -> list[str]:
        """
        Cellophane overlap segmentation over a list of unit_ids (mixed depths OK).
        Returns unit_ids that may include:
          - original unit_ids that were not merged (any depth)
          - newly created merged units whose depth = max(child.depth) + 1
        """
        length = len(unit_ids)
        if length <= 1:
            return list(unit_ids)

        step_matches = self._build_step_matches(unit_ids, pass_id)

        best: list[UnitChoice | None] = [None] * (length + 1)
        best[length] = UnitChoice([], length, 0.0, 0, 0)

        for start in range(length - 1, -1, -1):
            fallback = best[start + 1]
            assert fallback is not None

            best_choice = UnitChoice(
                segment_units=[unit_ids[start]],
                next_index=start + 1,
                support_score=fallback.support_score,
                supported_length=fallback.supported_length,
                piece_count=fallback.piece_count + 1,
            )

            for end in range(start + 2, length + 1):
                score = self._support_score(step_matches, start, end)
                if score == 0.0:
                    continue

                remainder = best[end]
                assert remainder is not None
                span = end - start

                # Merge: depth is computed from actual child depths
                child_ids = unit_ids[start:end]
                merged_content = "".join(self.get_unit_content(uid) for uid in child_ids)
                merged_id = self.get_or_create_unit(merged_content, child_ids)

                candidate = UnitChoice(
                    segment_units=[merged_id],
                    next_index=end,
                    support_score=remainder.support_score + score * span,
                    supported_length=remainder.supported_length + span,
                    piece_count=remainder.piece_count + 1,
                )

                if self._is_better(candidate, best_choice):
                    best_choice = candidate

            best[start] = best_choice

        result: list[str] = []
        idx = 0
        while idx < length:
            choice = best[idx]
            assert choice is not None
            result.extend(choice.segment_units)
            idx = choice.next_index
        return result

    # ------------------------------------------------------------------ #
    # Cellophane overlap helpers                                           #
    # ------------------------------------------------------------------ #

    def _build_step_matches(
        self, unit_ids: list[str], pass_id: int
    ) -> list[set[PathState]]:
        step_matches: list[set[PathState]] = []
        for i in range(len(unit_ids) - 1):
            rows = self.conn.execute(
                """
                SELECT sentence_id, position, opacity
                FROM unit_links
                WHERE source_unit_id = ? AND target_unit_id = ? AND pass_id = ?
                """,
                (unit_ids[i], unit_ids[i + 1], pass_id),
            ).fetchall()
            step_matches.append({
                (str(r["sentence_id"]), int(r["position"]), float(r["opacity"]))
                for r in rows
            })
        return step_matches

    def _support_score(
        self, step_matches: list[set[PathState]], start: int, end: int
    ) -> float:
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
            for s_id, pos, op in survivors:
                for ns_id, npos, nop in next_matches:
                    if ns_id == s_id and npos == pos + 1:
                        advanced.add((ns_id, npos, nop))

            survivors = advanced
            if not survivors:
                return 0.0

        return sum(op for _, _, op in survivors)

    def _is_better(self, candidate: UnitChoice, current: UnitChoice) -> bool:
        return (
            candidate.support_score,
            candidate.supported_length,
            -candidate.piece_count,
        ) > (
            current.support_score,
            current.supported_length,
            -current.piece_count,
        )

    def _ingest_unit_links(
        self, unit_ids: list[str], pass_id: int, sentence_id: str
    ) -> None:
        """
        Record transition links for this pass.
        link_depth = max(source.depth, target.depth)  — reflects actual unit depth.
        """
        if len(unit_ids) < 2:
            return

        rows = []
        for i in range(len(unit_ids) - 1):
            src, tgt = unit_ids[i], unit_ids[i + 1]
            link_depth = max(self.get_unit_depth(src), self.get_unit_depth(tgt))
            rows.append((src, tgt, sentence_id, i, pass_id, link_depth))

        self.conn.executemany(
            """
            INSERT INTO unit_links
              (source_unit_id, target_unit_id, sentence_id, position, pass_id, link_depth)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # Side-View Thinking                                                   #
    # ------------------------------------------------------------------ #

    def _think_side_view(
        self,
        current_sentence_id: str,
        pass_results: list[dict[str, Any]],
    ) -> list[str]:
        """
        Cross-sectional association.
        Uses unit_ids whose actual depth >= 2 (thought/concept layer).
        Filters particle plateaus and returns Top-20 by opacity weight.
        """
        active_ids: set[str] = set()
        for pr in pass_results:
            for uid, d in zip(pr["unit_ids"], pr["depths"]):
                if d >= 2:
                    active_ids.add(uid)

        if not active_ids:
            return []

        ph = ",".join("?" * len(active_ids))
        id_list = list(active_ids)

        rows = self.conn.execute(
            f"""
            SELECT DISTINCT sentence_id FROM unit_links
            WHERE (source_unit_id IN ({ph}) OR target_unit_id IN ({ph}))
              AND link_depth >= 2
              AND sentence_id != ?
            """,
            id_list + id_list + [current_sentence_id],
        ).fetchall()

        intersecting = [str(r["sentence_id"]) for r in rows]
        if not intersecting:
            return []

        sph = ",".join("?" * len(intersecting))
        assoc_rows = self.conn.execute(
            f"""
            SELECT u.content, SUM(ul.opacity) as total_weight
            FROM unit_links ul
            JOIN units u ON ul.target_unit_id = u.unit_id
            WHERE ul.sentence_id IN ({sph})
              AND ul.link_depth >= 2
              AND u.depth >= 2
            GROUP BY u.content
            ORDER BY total_weight DESC
            """,
            intersecting,
        ).fetchall()

        current_contents: set[str] = set()
        for pr in pass_results:
            current_contents.update(pr["contents"])

        raw: list[tuple[str, float]] = []
        for r in assoc_rows:
            w = str(r["content"]).strip()
            wt = float(r["total_weight"])
            if w and w not in current_contents and w not in [x for x, _ in raw]:
                raw.append((w, wt))

        if not raw:
            return []

        PARTICLE_SET = {
            "은", "는", "이", "가", "을", "를", "의", "에", "로", "으로",
            "도", "과", "와", "에서", "에게", "하며", "하고", "이며", "이다",
            "있다", "없다", "다.", "다", "한", "인", "은,",
        }

        max_w = raw[0][1] if raw else 1.0

        filtered: list[str] = []
        for word, wt in raw:
            if word in PARTICLE_SET:
                continue
            if len(word) == 1 and word in "은는이가을를의에도과와로한인":
                continue
            if wt >= max_w * 0.85 and any(
                word.endswith(p) for p in PARTICLE_SET if len(word) <= len(p) + 1
            ):
                continue
            filtered.append(word)

        return filtered[:20]
