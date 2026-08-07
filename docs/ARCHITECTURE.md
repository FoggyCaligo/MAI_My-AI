# MAI Architecture Draft

## 1. Core idea

MAI does not predefine fixed semantic layers such as `character -> word -> meaning -> concept -> object`.

Instead, it uses one recursive unit system.

```text
observation
  -> overlap / repeated pattern discovery
  -> new Unit
  -> feed the resulting Units back into the same process
  -> discover higher-order Units
  -> repeat
```

Human observers may call the resulting structures words, meanings, contexts, concepts, objects, rules, or something else. MAI itself should not need those labels in advance.

## 2. Unit and depth

The primitive input unit is a character.

```text
depth 0 = character
```

Every newly discovered Unit receives:

```text
new_depth = max(child.depth) + 1
```

The child Units do not have to share the same depth.

Examples:

```text
0 + 0       -> 1
1 + 1       -> 2
1 + 2       -> 3
0 + 3       -> 4
2 + 2 + 4   -> 5
```

Therefore `depth` is not a semantic class. It only records how deep the Unit is in the recursive composition history originating from characters.

A depth-3 Unit may directly include a depth-1 Unit if another part of the composition already reaches depth 2.

## 3. Recursive extraction

### Pass 1: character to discovered Unit

A sentence is first decomposed into characters.

The existing cellophane-style overlap method is applied over character occurrences. Repeated blocks become new Units.

In Korean, these depth-1 Units are expected to often resemble words including particles, but MAI should not hard-code that assumption.

### Pass 2 and beyond

The discovered Units are fed back into the same system.

The overlap method is then applied over those Units instead of characters.

This may discover rough usage, meaning, context, concepts, objects, relations, or other larger structures without defining separate engines for each category.

```text
characters (depth 0)
    -> discovered Units (depth 1)
    -> feed back
    -> discovered Units (depth 2)
    -> feed back
    -> discovered Units (depth 3)
    -> ...
```

## 4. Sentence lineage

Every original sentence receives a unique `sentence_id`.

Units derived from that sentence retain the same lineage information at every depth.

This preserves the fact that differently abstracted Units were observed in the same original event/context.

For example:

```text
sentence_id = 123

depth 0: [나][는][사][과][를][먹][었][다]
depth 1: [나는][사과를][먹었다]
depth 2: [derived Unit A][derived Unit B]
depth 3: [derived Unit C]
```

All of these remain traceable to sentence 123.

## 5. Composition lineage

`depth` alone is not enough. Every Unit must retain its construction lineage.

Conceptually:

```text
Unit
- unit_id
- depth
- support_count
- created_at
```

```text
UnitComposition
- parent_unit_id
- child_unit_id
- position
```

```text
Occurrence
- unit_id
- sentence_id
- position
- pass_id
```

This allows any abstract Unit to be expanded back toward its source observations.

## 6. Top view and side view

The cellophane metaphor can be viewed in two directions.

### Top view

From above, overlapping observations create visible blocks. These blocks are the Units discovered at each depth.

### Side view

From the side, Units created from the same sentence form a vertically nested structure across depths.

```text
          depth 3       U300
                          |
          depth 2       U210
                        /   \
          depth 1    U101   U102
                     / \     / \
          depth 0   characters...
```

This suggests that MAI should preserve not only composition edges, but also the fact that Units at multiple depths belong to the same derivation process.

A separate relation or occurrence record may represent this, for example:

```text
derived_in_same_pass
```

The exact storage representation is not fixed yet.

## 7. Think: current hypothesis

Think is not implemented yet. The current hypothesis is that thinking should use the same Unit structure rather than introduce a separate symbolic reasoning language.

A candidate process is:

```text
current input Units activate
    -> move vertically through their derivation structure
    -> move horizontally to related Units / other sentence-derived structures
    -> move back down when concrete detail is needed
    -> generate candidate compositions
    -> compare candidates
```

Two navigation axes emerge:

- vertical: abstraction <-> concretization
- horizontal: relation / similarity / shared structure across observations

### Candidate consistency criterion

A possible evaluation rule discussed so far is:

> Prefer a candidate that explains the current input while preserving as much existing structure as possible and requiring as little destructive rewiring as possible.

Potential signals include:

- amount of existing structure preserved
- number/strength of contradictions introduced
- number of existing links that would have to be removed
- number of unsupported new links required
- support from repeated observations

This is still an open design problem, not a finalized objective function.

## 8. Output sentence generation: current hypothesis

Thinking and language realization should remain separate.

A selected Unit or composition should not be converted to text by blindly expanding `made_from` links.

Instead, MAI should preserve how similar Units were actually expressed in past observations.

Conceptually:

```text
thought result
    -> find past realization / occurrence patterns
    -> choose a realization compatible with the current context
    -> expand toward lower-depth language Units
    -> characters / text
```

This allows output fluency to improve as more observed language accumulates.

## 9. Core vs application boundary

MAI should contain a reusable internal core while allowing UI and other interfaces to live outside that core.

The initial boundary is:

```text
core/
  reusable cognitive engine and storage abstractions

app/
  UI, CLI, API server, adapters, and product-facing integrations
```

The core must not depend on a particular UI.

Applications may depend on the core.

This allows desktop UI, web UI, CLI, or other interfaces to be replaced without changing the cognitive architecture.

## 10. Open questions

The following are intentionally unresolved:

- exact persistence schema for Units, compositions, occurrences, and derivation passes
- exact implementation of the cellophane overlap operation for arbitrary depth
- activation and retrieval strategy
- how horizontal relations should be created and weakened
- Think candidate generation
- formal consistency / contradiction scoring
- forgetting / compression / pruning
- realization selection for output language
- how non-language sensory inputs may eventually enter the same Unit system

This document is an architectural RFC, not a frozen specification.
