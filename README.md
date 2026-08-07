# MAI (My AI)

MAI is an experimental personal AI architecture project based on a recursive cellophane overlap model, side-view cross-sectional thinking, and 0~100 score opacity feedback.

## Features

- **Recursive Multi-Depth Unit Engine (`core/`)**:
  - Automatically segments sentences from characters (`depth 0`) into words (`depth 1`), meanings (`depth 2`), and concepts (`depth N`).
  - Recurses until the entire input sentence merges into a single Unit or reaches maximum layering.
- **Pure Graph Database (`mai_core.db`)**:
  - Stores `units`, `compositions`, and `unit_links` with `sentence_id` lineage and `opacity` weights.
- **Side-View Thinking**:
  - Explores cross-sectional intersections of past sentence layers at `depth >= 2` to generate associative thoughts.
- **0~100 Score Opacity Feedback**:
  - Adjusts cellophane layer opacity for thought units (`depth >= 2`), effectively suppressing high-frequency particles or wrong associations upon negative feedback.

## Getting Started

Run the interactive CLI:

```bash
python -m app.main
```

Or:

```bash
python app/cli.py
```
