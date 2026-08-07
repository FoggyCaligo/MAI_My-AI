# Cellophane Storage and Top-View Matching

## 1. Mental model

MAI treats stored observations like transparent sentence layers stacked above reusable Units.

At the lowest level, the floor is made of character Units. Higher Units may be created recursively, but the same storage rule applies at every level.

Each cell contains observation elements that say:

```text
from this Unit
follow this next Unit address
inside this sentence
at this position
```

Conceptually:

```text
CellElement
- source_unit_id
- next_unit_id
- sentence_id
- position
- pass_id
- cell_depth
- opacity
```

`next_unit_id` is the practical equivalent of a directly followable address. `sentence_id` and `position` preserve the original observation path.

## 2. Persistent table, in-memory 3D view

SQLite remains the persistent representation, but the overlap algorithm should not repeatedly query every candidate edge.

For the Units that occur in the current input, MAI loads their cells once and builds an in-memory index:

```text
source Unit
  -> next Unit
      -> (sentence_id, position)
          -> opacity
```

This is the implementation equivalent of looking vertically through stacked sentence sheets.

## 3. Overlaying the current sentence

Suppose the current path is:

```text
A -> B -> C -> D
```

At `A -> B`, all historical sentence layers containing that exact address transition are initially visible.

At the next step, a layer survives only if the same `sentence_id` also contains `B -> C` at the immediately following position.

The same rule repeats for `C -> D`.

Therefore two unrelated observations cannot be stitched into a fake path:

```text
sentence X: A -> B
sentence Y: B -> C
```

does not imply:

```text
A -> B -> C
```

because the `sentence_id` lineage is different.

## 4. What the top view counts

When opacity is `1.0`, every surviving sentence layer contributes one visible layer for every traversed address cell.

For a candidate span:

```text
visible cell count
= surviving sentence layers * traversed edges
```

Opacity feedback turns this into a weighted visible-cell count without changing the underlying path identity.

This replaces repeated SQL span matching with:

```text
load relevant cells once
-> keep matching sentence paths alive in memory
-> count the cells that remain visible
```

## 5. Pass and depth remain separate

`pass_id` records when an observation was produced during recursive processing. It is not part of structural equality for top-view matching.

`Unit.depth` still follows the recursive composition rule:

```text
new_depth = max(child.depth) + 1
```

Mixed-depth sequences are allowed and are passed forward unchanged until a new composition is actually discovered.

## 6. Persistence compatibility

Older development databases may contain `unit_links`. On startup, MAI copies compatible legacy rows into `cell_elements` with an idempotent migration.

The old table is left untouched so early experimental databases can still be opened safely while the engine uses the new cell representation.
