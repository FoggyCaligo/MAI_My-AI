# MAI Core

The `core` directory contains the reusable cognitive engine and persistent cellophane storage model.

## Structure

```text
core/
  db.py       : SQLite persistence and legacy migration
                - units
                - compositions
                - cell_elements
  engine.py   : CognitiveEngine
                - raw CellElement density lookup
                - recursive mixed-depth Unit discovery
                - lazy projection of existing higher Units
                - side-view associative thinking
                - 0~100 opacity feedback
  __init__.py : Core module interface
```

## Cellophane storage

Each observed transition is stored as one element inside the logical cell of its source Unit:

```text
source_unit_id
next_unit_id      # directly followable next-Unit address
sentence_id       # original observation identity
position          # order inside the stored layer
pass_id           # recursive pass coordinate of that observation
link_depth        # max depth of the linked Units
opacity
```

SQLite is the persistent store. The engine lazily loads only the source cells touched by the active sequence and keeps them in an in-memory cache.

Top-view density is structural:

```text
density(A -> B) = number of CellElements whose next address is B
```

No separate semantic confidence is required to define that density.

Historical `(sentence_id, pass_id, position)` remains available to preserve the original observation path and side-view coordinates. The current processing pass does not need to equal the historical pass where a matching structure was observed.

## Lazy projection

Past observations are never retroactively rewritten when a new Unit is learned.

Higher Units exist independently through `compositions`. When an old sentence becomes active again, the engine overlays currently known compositions on the stored base sequence in memory. This changes only the current active view, not the persisted historical observation.

Unit depth remains independent from `pass_id` and follows:

```text
new_depth = max(child.depth) + 1
```

Mixed-depth sequences are valid and lower Units remain permanently reusable.

The core engine remains independent of UI, CLI, web framework, or product-shell code.
