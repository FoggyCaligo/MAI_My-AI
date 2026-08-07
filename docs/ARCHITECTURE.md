# MAI Architecture Specification

## 1. Core Idea

MAI does not use fixed, hard-coded semantic layers. Instead, it employs a single recursive **Cellophane Overlap Unit Engine**.

```text
Observation (Sentence)
  -> Cellophane Overlap & Path Filtering (Depth 0: Characters -> Depth 1: Words)
  -> Recursive Feeding into Higher Depths (Depth 1 -> Depth 2: Meanings -> Depth N)
  -> Single Unit Termination Condition
  -> Post-Termination Side-View Thinking
  -> 0~100 Score Opacity Feedback Adjustment
```

---

## 2. Graph Storage Schema (SQLite: `mai_core.db`)

The database stores only pure graph units and transition links. No redundant raw text sentences table is needed; sentence lineage is preserved entirely via `sentence_id`.

- **`units`**:
  - `unit_id` (PRIMARY KEY)
  - `depth` (INTEGER: 0, 1, 2, ...)
  - `content` (TEXT)
  - `support_count` (INTEGER)
- **`compositions`**:
  - `parent_unit_id` -> `child_unit_id` (Positioned child composition)
- **`unit_links`**:
  - `source_unit_id`, `target_unit_id`, `sentence_id`, `position`, `depth`
  - `opacity` (REAL DEFAULT 1.0): Cellophane layer opacity / weight adjusted by user feedback.

---

## 3. Recursive Vertical Layering & Termination

1. **Pass 0 (Character to Word, Depth 0 -> 1)**:
   - Decomposes sentence into character units (`depth 0`).
   - Applies cellophane overlap path matching to extract word units (`depth 1`).
2. **Recursive Feeding (Depth 1 -> Depth 2 -> ... -> Depth N)**:
   - Output units at `depth K` become the input sequence for `depth K+1`.
3. **Termination Condition**:
   - The loop continues until the **entire input sentence is merged into a single top-level Unit** (`len(units) == 1`), or until no further reduction occurs for the current history state.

---

## 4. Side-View Thinking (Cross-Sectional Association)

Thinking is implemented via the **Side-View Cross-Sectional View**:

- Triggered **exclusively after vertical layering reaches its termination condition**.
- Collects active `unit_id`s from the current sentence at **`depth >= 2`** (thought/concept layers).
- Finds all intersecting `sentence_id` layers in `unit_links` (`depth >= 2`).
- Extracts all cross-sectional associative units present on those intersecting layers without arbitrary limit restrictions.

---

## 5. 0~100 Score Cellophane Opacity Feedback System

To resolve the problem of high-frequency particles (조사) polluting thinking layers, MAI incorporates user feedback:

- **Score Range**: 0 to 100
  - `50 points` (Neutral): Opacity multiplier = 1.0 (No change).
  - `> 50 points` (Positive / Reinforcement): Subtle increase (+0.5% per point above 50, up to +25% at 100 points, multiplier = 1.25).
  - `< 50 points` (Negative / Suppression): Subtle decrease (-0.5% per point below 50, down to -25% at 0 points, multiplier = 0.75).
- **Targeted Scope**: Applied **exclusively to thought layers (`depth >= 2`)**. `Depth 0` and `Depth 1` remain untouched so basic word parsing stability is never degraded.

---

## 6. Core vs Application Architecture

```text
core/
  db.py       : Pure SQLite graph database manager and automatic migration
  engine.py   : CognitiveEngine (Recursive cellophane layering, side-view thinking, feedback)
  __init__.py : Exports CognitiveEngine

app/
  cli.py      : Interactive CLI interface for processing sentences & entering 0-100 feedback
  main.py     : Application entry point
```
