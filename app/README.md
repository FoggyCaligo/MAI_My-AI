# MAI Application Layer

The `app` directory is reserved for interfaces built on top of MAI Core.

Examples include:

- web UI
- desktop UI
- CLI
- API server
- external adapters

Applications may depend on `core`, but `core` should not depend on anything inside `app`.

This keeps the cognitive engine reusable while allowing product-facing interfaces to evolve independently.
