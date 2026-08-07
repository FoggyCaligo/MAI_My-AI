from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Database

LayerKey = tuple[str, int, int]  # (sentence_id, stored_pass_id, position)


@dataclass(slots=True)
class CellElement:
    next_unit_id: str
    sentence_id: str
    pass_id: int
    position: int
    opacity: float


class CognitiveEngine:
    """
    Recursive MAI engine built around logical cellophane cells.

    Each source Unit owns a logical cell containing many CellElements.
    A CellElement stores an address-like next_unit_id together with the
    sentence/pass/position coordinates of the observation that created it.

    Top view:
      follow only the current input's next_unit_id sequence and count how many
      historical sentence layers survive. pass_id is not required to equal the
      current processing pass; it is only a coordinate inside the stored layer.

    Side view:
      sentence_id + pass_id + position and compositions preserve the vertical
      derivation history, so the same storage can reconstruct cross sections.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db = Database(db_path)
        self.conn = self.db.conn
        # source -> next -> {(sentence_id, stored_pass_id, position): opacity}
        self._cell_cache: dict[str, dict[str, dict[LayerKey, float]]] = {}

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

    def get_or_create_unit(
        self,
        content: str,
        child_unit_ids: list[str] | None = None,
    ) -> str:
        if not child_unit_ids:
            depth = 0
        else:
            depth = max(self.get_unit_depth(uid) for uid in child_unit_ids) + 1

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
                """
                INSERT INTO compositions (parent_unit_id, child_unit_id, position)
                VALUES (?, ?, ?)
                """,
                [(unit_id, child_id, pos) for pos, child_id in enumerate(child_unit_ids)],
            )

        self.conn.commit()
        return unit_id

    # ------------------------------------------------------------------ #
    # Cell cache / top-view storage                                        #
    # ------------------------------------------------------------------ #

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

    def _matching_paths(
        self,
        source_unit_id: str,
        next_unit_id: str,
        exclude_sentence_id: str,
    ) -> dict[LayerKey, float]:
        paths = self._cell_targets(source_unit_id).get(next_unit_id, {})
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
        if not survivors:
            return {}

        next_paths = self._cell_targets(source_unit_id).get(next_unit_id, {})
        advanced: dict[LayerKey, float] = {}

        for (sentence_id, stored_pass_id, position), opacity in survivors.items():
            wanted = (sentence_id, stored_pass_id, position + 1)
            next_opacity = next_paths.get(wanted)
            if next_opacity is not None:
                # Keep the weakest visible point of the surviving historical layer.
                advanced[wanted] = min(opacity, next_opacity)

        return advanced

    def _record_cell_elements(
        self,
        unit_ids: list[str],
        pass_id: int,
        sentence_id: str,
    ) -> None:
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

        # Keep already-loaded logical cells coherent without re-querying SQLite.
        for source, next_id, sid, position, stored_pass_id, _ in rows:
            cached = self._cell_cache.get(source)
            if cached is not None:
                cached.setdefault(next_id, {})[(sid, stored_pass_id, position)] = 1.0

    # ------------------------------------------------------------------ #
    # Top-view segmentation                                                #
    # ------------------------------------------------------------------ #

    def _segment_units(
        self,
        unit_ids: list[str],
        current_sentence_id: str,
    ) -> list[str]:
        """
        Overlay the current sequence on the logical cells.

        At each start position, historical layers that point somewhere else are
        discarded one Unit at a time. The surviving CellElement count is the
        top-view density for that span. The span with the largest surviving
        layer count is selected; equal counts prefer the longer span.

        No candidate Unit is written while evaluating spans. Only the finally
        selected blocks are materialized in the database.
        """
        length = len(unit_ids)
        if length <= 1:
            return list(unit_ids)

        result: list[str] = []
        start = 0

        while start < length:
            if start == length - 1:
                result.append(unit_ids[start])
                break

            survivors = self._matching_paths(
                unit_ids[start],
                unit_ids[start + 1],
                current_sentence_id,
            )

            best_end = start + 1
            best_count = 0

            if survivors:
                best_end = start + 2
                best_count = len(survivors)

                cursor = start + 1
                while cursor < length - 1:
                    survivors = self._advance_paths(
                        survivors,
                        unit_ids[cursor],
                        unit_ids[cursor + 1],
                    )
                    if not survivors:
                        break

                    end = cursor + 2
                    count = len(survivors)
                    if count > best_count or (count == best_count and end > best_end):
                        best_count = count
                        best_end = end
                    cursor += 1

            if best_count == 0:
                result.append(unit_ids[start])
                start += 1
                continue

            child_ids = unit_ids[start:best_end]
            content = "".join(self.get_unit_content(uid) for uid in child_ids)
            result.append(self.get_or_create_unit(content, child_ids))
            start = best_end

        return result

    # ------------------------------------------------------------------ #
    # Feedback                                                             #
    # ------------------------------------------------------------------ #

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

        # Feedback changed persistent visibility; refresh lazily on next access.
        self._cell_cache.clear()
        return new_avg

    # ------------------------------------------------------------------ #
    # Main processing loop                                                 #
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
        current_unit_ids = [self.get_or_create_unit(ch) for ch in normalized]

        pass_results: list[dict[str, Any]] = []
        pass_id = 0
        max_pass_limit = 100

        while pass_id < max_pass_limit:
            segmented_ids = self._segment_units(current_unit_ids, sentence_id)

            # Store the exact layer that was seen before moving upward.
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
                }
            )

            if len(segmented_ids) == 1:
                break
            if segmented_ids == current_unit_ids:
                break

            current_unit_ids = segmented_ids
            pass_id += 1

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
    # Side-view thinking                                                   #
    # ------------------------------------------------------------------ #

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
