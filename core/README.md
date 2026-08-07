# MAI Core

The `core` directory contains the reusable cognitive engine and persistent cellophane storage model.

## Structure

```text
core/
  db.py       : SQLite schema and legacy migration
                - units
                - compositions
                - cell_elements
  engine.py   : CognitiveEngine
                - recursive mixed-depth Unit extraction
                - in-memory cellophane top-view overlap
                - side-view associative thinking
                - 0~100 opacity feedback
  __init__.py : Core module interface
```

## Cellophane storage

MAI stores each observed transition as a cell element:

```text
source_unit_id
next_unit_id      # directly followable address
sentence_id       # original observation identity
position          # order inside that observation/pass
pass_id           # recursive processing metadata
cell_depth
opacity
```

SQLite is the persistent store. During segmentation, the engine loads the relevant source cells in one query and builds an in-memory index:

```text
source Unit
  -> next Unit
      -> (sentence_id, historical_pass_id, position)
          -> opacity
```

The current input is overlaid on this index. A historical path survives only while the next address matches and the same historical `(sentence_id, pass_id)` continues at `position + 1`. This prevents unrelated sentences or different passes of the same sentence from being stitched into a fake path.

The current processing pass does not have to equal the historical pass where a structure was observed. Unit depth remains independent and follows:

```text
new_depth = max(child.depth) + 1
```

The core engine remains independent of UI or CLI application layers.
