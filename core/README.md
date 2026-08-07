# MAI Core

The `core` directory is reserved for the reusable cognitive engine.

It should own the recursive Unit model, depth/lineage rules, storage abstractions, extraction, activation, thinking, and realization logic.

The core must not depend on a specific UI, web framework, CLI, or product shell.

Planned internal areas may eventually include:

```text
core/
  unit/
  extraction/
  memory/
  think/
  realization/
  storage/
```

These folders are intentionally not created yet because their boundaries are still under design.
