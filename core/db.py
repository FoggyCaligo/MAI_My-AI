from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS units (
    unit_id TEXT PRIMARY KEY,
    depth INTEGER NOT NULL,
    content TEXT NOT NULL,
    support_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_units_depth_content ON units (depth, content);

CREATE TABLE IF NOT EXISTS compositions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_unit_id TEXT NOT NULL,
    child_unit_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    FOREIGN KEY (parent_unit_id) REFERENCES units(unit_id),
    FOREIGN KEY (child_unit_id) REFERENCES units(unit_id)
);

CREATE INDEX IF NOT EXISTS idx_compositions_parent ON compositions (parent_unit_id);
CREATE INDEX IF NOT EXISTS idx_compositions_child ON compositions (child_unit_id);

-- A row is one element stacked inside the logical cell of source_unit_id.
-- next_unit_id is the direct address-like pointer to the next Unit.
-- sentence_id + pass_id + position identifies the original cellophane layer.
CREATE TABLE IF NOT EXISTS cell_elements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_unit_id TEXT NOT NULL,
    next_unit_id TEXT NOT NULL,
    sentence_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    pass_id INTEGER NOT NULL,
    link_depth INTEGER NOT NULL,
    opacity REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_unit_id) REFERENCES units(unit_id),
    FOREIGN KEY (next_unit_id) REFERENCES units(unit_id)
);

CREATE INDEX IF NOT EXISTS idx_cell_elements_source_next
ON cell_elements (source_unit_id, next_unit_id);

CREATE INDEX IF NOT EXISTS idx_cell_elements_layer
ON cell_elements (sentence_id, pass_id, position);

CREATE INDEX IF NOT EXISTS idx_cell_elements_depth
ON cell_elements (link_depth);

CREATE INDEX IF NOT EXISTS idx_cell_elements_source_sentence
ON cell_elements (source_unit_id, sentence_id);

-- Exact Unit slots of every observed sentence/pass layer. Unlike an edge-only
-- representation, this also preserves single-Unit layers for Expression View.
CREATE TABLE IF NOT EXISTS observed_layer_units (
    sentence_id TEXT NOT NULL,
    pass_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    unit_id TEXT NOT NULL,
    PRIMARY KEY (sentence_id, pass_id, position),
    FOREIGN KEY (unit_id) REFERENCES units(unit_id)
);

CREATE INDEX IF NOT EXISTS idx_observed_layer_units_unit
ON observed_layer_units (unit_id, sentence_id, pass_id);

CREATE TABLE IF NOT EXISTS thoughts (
    thought_id TEXT PRIMARY KEY,
    thought_sequence INTEGER,
    source_sentence_id TEXT,
    status TEXT NOT NULL DEFAULT 'complete',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_thoughts_source_sentence
ON thoughts (source_sentence_id);

CREATE TABLE IF NOT EXISTS thought_elements (
    element_id TEXT PRIMARY KEY,
    thought_id TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    thought_position INTEGER NOT NULL,
    branch_id TEXT NOT NULL,
    parent_element_id TEXT,
    status TEXT NOT NULL,
    observed_density INTEGER NOT NULL DEFAULT 0,
    thought_density INTEGER NOT NULL DEFAULT 0,
    thought_visibility REAL NOT NULL DEFAULT 0.0,
    opacity REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (thought_id) REFERENCES thoughts(thought_id),
    FOREIGN KEY (unit_id) REFERENCES units(unit_id),
    FOREIGN KEY (parent_element_id) REFERENCES thought_elements(element_id)
);

CREATE INDEX IF NOT EXISTS idx_thought_elements_thought
ON thought_elements (thought_id, thought_position);

CREATE INDEX IF NOT EXISTS idx_thought_elements_unit_status
ON thought_elements (unit_id, status);

-- A ThoughtElement occupies its own Unit cell and every real Unit cell reached
-- while recursively expanding its immutable composition.
CREATE TABLE IF NOT EXISTS thought_footprints (
    element_id TEXT NOT NULL,
    footprint_unit_id TEXT NOT NULL,
    footprint_kind TEXT NOT NULL,
    composition_distance INTEGER NOT NULL,
    PRIMARY KEY (element_id, footprint_unit_id),
    FOREIGN KEY (element_id) REFERENCES thought_elements(element_id),
    FOREIGN KEY (footprint_unit_id) REFERENCES units(unit_id)
);

CREATE INDEX IF NOT EXISTS idx_thought_footprints_unit
ON thought_footprints (footprint_unit_id, element_id);

CREATE TABLE IF NOT EXISTS thought_edges (
    edge_id TEXT PRIMARY KEY,
    thought_id TEXT NOT NULL,
    source_element_id TEXT NOT NULL,
    target_element_id TEXT NOT NULL,
    status TEXT NOT NULL,
    observed_density INTEGER NOT NULL DEFAULT 0,
    thought_density INTEGER NOT NULL DEFAULT 0,
    thought_visibility REAL NOT NULL DEFAULT 0.0,
    opacity REAL NOT NULL DEFAULT 1.0,
    FOREIGN KEY (thought_id) REFERENCES thoughts(thought_id),
    FOREIGN KEY (source_element_id) REFERENCES thought_elements(element_id),
    FOREIGN KEY (target_element_id) REFERENCES thought_elements(element_id)
);

CREATE INDEX IF NOT EXISTS idx_thought_edges_thought
ON thought_edges (thought_id);
"""


def get_default_db_path() -> Path:
    return Path(__file__).resolve().parent / "mai_core.db"


class Database:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else get_default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate_legacy_unit_links()
        self._migrate_observed_layer_units()
        self._migrate_thought_projection()
        self._migrate_thought_visibility()
        self.conn.commit()

    def _table_exists(self, table_name: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    def _migrate_legacy_unit_links(self) -> None:
        """Copy legacy unit_links rows into cell_elements without duplicating observations.

        Migration is row-idempotent rather than all-or-nothing: if an earlier run copied
        only part of a legacy table, the missing rows can still be imported later.
        """
        if not self._table_exists("unit_links"):
            return

        columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(unit_links)").fetchall()
        }
        required = {"source_unit_id", "target_unit_id", "sentence_id", "position"}
        if not required.issubset(columns):
            return

        pass_expr = "ul.pass_id" if "pass_id" in columns else "0"
        if "link_depth" in columns:
            depth_expr = "ul.link_depth"
        elif "depth" in columns:
            depth_expr = "ul.depth"
        else:
            depth_expr = "0"
        opacity_expr = "ul.opacity" if "opacity" in columns else "1.0"

        self.conn.execute(
            f"""
            INSERT INTO cell_elements
              (source_unit_id, next_unit_id, sentence_id, position, pass_id, link_depth, opacity)
            SELECT
              ul.source_unit_id,
              ul.target_unit_id,
              ul.sentence_id,
              ul.position,
              {pass_expr},
              {depth_expr},
              {opacity_expr}
            FROM unit_links AS ul
            WHERE NOT EXISTS (
                SELECT 1
                FROM cell_elements AS ce
                WHERE ce.source_unit_id = ul.source_unit_id
                  AND ce.next_unit_id = ul.target_unit_id
                  AND ce.sentence_id = ul.sentence_id
                  AND ce.position = ul.position
                  AND ce.pass_id = {pass_expr}
            )
            """
        )

    def _migrate_observed_layer_units(self) -> None:
        """Reconstruct missing observed Unit slots from existing CellElements."""
        self.conn.execute(
            """
            INSERT OR IGNORE INTO observed_layer_units
              (sentence_id, pass_id, position, unit_id)
            SELECT sentence_id, pass_id, position, source_unit_id
            FROM cell_elements
            """
        )
        self.conn.execute(
            """
            INSERT OR IGNORE INTO observed_layer_units
              (sentence_id, pass_id, position, unit_id)
            SELECT ce.sentence_id, ce.pass_id, ce.position + 1, ce.next_unit_id
            FROM cell_elements AS ce
            WHERE NOT EXISTS (
                SELECT 1
                FROM cell_elements AS later
                WHERE later.sentence_id = ce.sentence_id
                  AND later.pass_id = ce.pass_id
                  AND later.position = ce.position + 1
            )
            """
        )

    def _migrate_thought_projection(self) -> None:
        """Add stable append order required by the computed Thought view."""
        columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(thoughts)").fetchall()
        }
        if "thought_sequence" not in columns:
            self.conn.execute("ALTER TABLE thoughts ADD COLUMN thought_sequence INTEGER")

        self.conn.execute(
            """
            UPDATE thoughts
            SET thought_sequence = rowid
            WHERE thought_sequence IS NULL
            """
        )
        self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_thoughts_sequence
            ON thoughts (thought_sequence)
            """
        )

    def _migrate_thought_visibility(self) -> None:
        """Add computed Thought-view evidence columns to older databases."""
        for tableName in ("thought_elements", "thought_edges"):
            columns = {
                str(row["name"])
                for row in self.conn.execute(
                    f"PRAGMA table_info({tableName})"
                ).fetchall()
            }
            if "thought_visibility" not in columns:
                self.conn.execute(
                    f"ALTER TABLE {tableName} "
                    "ADD COLUMN thought_visibility REAL NOT NULL DEFAULT 0.0"
                )

    def close(self) -> None:
        self.conn.close()
