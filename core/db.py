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
CREATE INDEX IF NOT EXISTS idx_compositions_child  ON compositions (child_unit_id);

CREATE TABLE IF NOT EXISTS unit_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_unit_id TEXT NOT NULL,
    target_unit_id TEXT NOT NULL,
    sentence_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    pass_id INTEGER NOT NULL,
    link_depth INTEGER NOT NULL,
    opacity REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_unit_links_pair_pass
ON unit_links (source_unit_id, target_unit_id, pass_id);

CREATE INDEX IF NOT EXISTS idx_unit_links_sentence_pass
ON unit_links (sentence_id, pass_id);

CREATE INDEX IF NOT EXISTS idx_unit_links_link_depth
ON unit_links (link_depth);

CREATE INDEX IF NOT EXISTS idx_unit_links_source_sentence
ON unit_links (source_unit_id, sentence_id);
"""


def get_default_db_path() -> Path:
    return Path(__file__).resolve().parent / "mai_core.db"


class Database:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else get_default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._ensure_migrations()
        self.conn.commit()

    def _ensure_migrations(self) -> None:
        # unit_links migrations
        cursor = self.conn.execute("PRAGMA table_info(unit_links)")
        columns = {row["name"] for row in cursor.fetchall()}
        if columns:
            if "opacity" not in columns:
                self.conn.execute(
                    "ALTER TABLE unit_links ADD COLUMN opacity REAL NOT NULL DEFAULT 1.0"
                )
            if "pass_id" not in columns:
                self.conn.execute(
                    "ALTER TABLE unit_links ADD COLUMN pass_id INTEGER NOT NULL DEFAULT 0"
                )
            if "link_depth" not in columns:
                self.conn.execute(
                    "ALTER TABLE unit_links ADD COLUMN link_depth INTEGER NOT NULL DEFAULT 0"
                )
            # depth column is legacy — keep it but alias queries use link_depth
            # Remove old depth-based index silently if possible
            try:
                self.conn.execute("DROP INDEX IF EXISTS idx_unit_links_pair_depth")
                self.conn.execute("DROP INDEX IF EXISTS idx_unit_links_sentence_depth")
            except Exception:
                pass

    def close(self) -> None:
        self.conn.close()
