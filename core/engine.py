from __future__ import annotations

import uuid
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


class CognitiveEngine:
    """Recursive MAI engine built around logical cellophane cells.

    Persistent observations are never retroactively rewritten.

    Top view:
      each source Unit is a logical cell containing observed next-unit addresses.
      For the currently active sequence, the number of matching CellElements is
      the density itself. No separate semantic score is required.

    Lazy projection:
      higher-depth Units live independently from the observations that first
      produced their children. When an old sentence becomes active again,
      existing compositions are overlaid on that active sequence without
      rewriting the old observation.

    Side view:
      sentence_id + pass_id + position preserve observed layers, while
      compositions preserve vertical parent/child structure.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db = Database(db_path)
        self.conn = self.db.conn
        self._cell_cache: dict[str, dict[str, dict[LayerKey, float]]] = {}
        self._composition_cache: dict[str, list[CompositionCandidate]] = {}

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

        # Same surface content at the same depth may still be a different Unit
        # when its composition lineage is different.
        unit_id = f"unit-d{depth}-{uuid.uuid4().hex[:8]}"
        self.conn.execute(
            "INSERT INTO units (unit_id, depth, content, support_count) VALUES (?, ?, ?, 1)",
            (unit_id, depth, content),
        )
        if child_unit_ids:
            self._record_composition(unit_id, child_unit_ids)
        self.conn.commit()
        return unit_id

    # ------------------------------------------------------------------ #
    # Cell storage / raw top-view density                                  #
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

    def _edge_density(
        self,
        source_unit_id: str,
        next_unit_id: str,
        exclude_sentence_id: str | None = None,
    ) -> int:
        """Return the literal number of matching observed CellElements."""
        paths = self._cell_targets(source_unit_id).get(next_unit_id, {})
        if exclude_sentence_id is None:
            return len(paths)
        return sum(1 for key in paths if key[0] != exclude_sentence_id)

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

        for source, next_id, sid, position, stored_pass_id, _ in rows:
            cached = self._cell_cache.get(source)
            if cached is not None:
                cached.setdefault(next_id, {})[(sid, stored_pass_id, position)] = 1.0

    # ------------------------------------------------------------------ #
    # Lazy projection from existing composition structure                 #
    # ------------------------------------------------------------------ #

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
        """Overlay already-known higher Units on an active sequence.

        This is read-only with respect to observations: old sentence layers are
        not rewritten. The returned list is only the current active view.
        """
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
        """Read-only lazy side-view projection for a stored sentence."""
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

    # ------------------------------------------------------------------ #
    # Density-driven discovery                                             #
    # ------------------------------------------------------------------ #

    def _discover_from_density(
        self,
        unit_ids: list[str],
        current_sentence_id: str,
    ) -> tuple[list[str], list[int]]:
        """Create new Units directly from visible overlap in the active layer."""
        if len(unit_ids) <= 1:
            return list(unit_ids), []

        densities = [
            self._edge_density(unit_ids[i], unit_ids[i + 1], current_sentence_id)
            for i in range(len(unit_ids) - 1)
        ]

        result: list[str] = []
        index = 0
        while index < len(unit_ids):
            if index >= len(densities) or densities[index] == 0:
                result.append(unit_ids[index])
                index += 1
                continue

            end = index + 1
            while end < len(densities) and densities[end] > 0:
                end += 1

            child_ids = unit_ids[index : end + 1]
            content = "".join(self.get_unit_content(uid) for uid in child_ids)
            result.append(self.get_or_create_unit(content, child_ids))
            index = end + 1

        return result, densities

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
