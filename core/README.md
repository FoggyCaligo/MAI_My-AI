# MAI Core

The `core` directory contains the reusable cognitive engine and graph storage model.

## Structure

```text
core/
  db.py       : SQLite graph database initialization, schema, and migration
  engine.py   : CognitiveEngine implementation
                - _segment_units_at_depth(): Cellophane overlap unit segmentation
                - process_sentence(): Recursive vertical layering loop
                - _think_side_view(): Cross-sectional associative thinking (depth >= 2)
                - apply_feedback(): 0~100 score cellophane opacity adjustment (depth >= 2)
  __init__.py : Core module interface
```

The core engine operates independently of UI or CLI application layers.
