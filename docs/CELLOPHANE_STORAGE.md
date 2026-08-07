# Cellophane Storage and Top-View Matching

## 1. Mental model

MAI treats stored observations like transparent sentence layers stacked above reusable Units.

At the lowest level, the floor is made of character Units. Higher Units may be created recursively, but the same storage rule applies at every level.

Each cell contains observation elements that say:

```text
from this Unit
follow this next Unit address
inside this sentence
inside this recursive pass
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

`next_unit_id` is the practical equivalent of a directly followable address. `sentence_id + pass_id + position` preserves one historical observation path.

## 2. Persistent table, in-memory 3D view

SQLite remains the persistent representation, but the overlap algorithm does not repeatedly query every candidate edge.

For the Units that occur in the current input, MAI loads their cells once and builds an in-memory index:

```text
source Unit
  -> next Unit
      -> (sentence_id, historical_pass_id, position)
          -> opacity
```

This is the implementation equivalent of looking vertically through stacked sentence sheets.

## 3. Overlaying the current sentence

Suppose the current path is:

```text
A -> B -> C -> D
```

At `A -> B`, all historical layers containing that exact address transition are initially visible.

At the next step, a layer survives only if the same historical `(sentence_id, pass_id)` also contains `B -> C` at the immediately following position.

The same rule repeats for `C -> D`.

Therefore unrelated observations cannot be stitched into a fake path:

```text
sentence X / pass 0: A -> B
sentence Y / pass 0: B -> C
```

and even different recursive passes of the same sentence cannot be stitched accidentally:

```text
sentence X / pass 0: A -> B
sentence X / pass 3: B -> C
```

Neither case implies one historical path `A -> B -> C`.

## 4. What the top view counts

When opacity is `1.0`, every surviving historical layer contributes one visible layer for every traversed address cell.

For a candidate span:

```text
visible cell count
= surviving historical layers * traversed edges
```

Opacity feedback turns this into a weighted visible-cell count without changing the underlying path identity.

This replaces repeated SQL span matching with:

```text
load relevant cells once
-> keep matching historical paths alive in memory
-> count the cells that remain visible
```

## 5. Pass and depth remain separate

The current processing pass does not need to equal the historical pass in which a matching structure was observed. `pass_id` is not a semantic depth or Unit identity.

However, once MAI starts following one historical layer, that layer keeps the same historical `pass_id` until the path ends. This prevents edges from different recursive representations of one sentence from being mixed together.

`Unit.depth` still follows the recursive composition rule:

```text
new_depth = max(child.depth) + 1
```

Mixed-depth sequences are allowed and are passed forward unchanged until a new composition is actually discovered.

## 6. Persistence compatibility

Older development databases may contain `unit_links`. On startup, MAI copies compatible legacy rows into `cell_elements` with an idempotent migration.

The old table is left untouched so early experimental databases can still be opened safely while the engine uses the new cell representation.
