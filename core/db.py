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
        self.conn.commit()

    def _table_exists(self, table_name: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    def _migrate_legacy_unit_links(self) -> None:
        """Copy old unit_links rows once into cell_elements when upgrading an existing DB."""
        if not self._table_exists("unit_links"):
            return

        existing = self.conn.execute("SELECT COUNT(*) AS c FROM cell_elements").fetchone()
        if existing and int(existing["c"]) > 0:
            return

        columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(unit_links)").fetchall()
        }
        required = {"source_unit_id", "target_unit_id", "sentence_id", "position"}
        if not required.issubset(columns):
            return

        pass_expr = "pass_id" if "pass_id" in columns else "0"
        if "link_depth" in columns:
            depth_expr = "link_depth"
        elif "depth" in columns:
            depth_expr = "depth"
        else:
            depth_expr = "0"
        opacity_expr = "opacity" if "opacity" in columns else "1.0"

        self.conn.execute(
            f"""
            INSERT INTO cell_elements
              (source_unit_id, next_unit_id, sentence_id, position, pass_id, link_depth, opacity)
            SELECT
              source_unit_id,
              target_unit_id,
              sentence_id,
              position,
              {pass_expr},
              {depth_expr},
              {opacity_expr}
            FROM unit_links
            """
        )

    def close(self) -> None:
        self.conn.close()
