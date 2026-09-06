# Working on Code Search Lab

This is an independent, local source-retrieval tool. No source project, business
domain, editor, agent platform or external service is assumed.

Use the current working directory as the source, or pass `--repo path/to/project`.
When using the tool from another checkout, invoke its Python entry point by path.
Use `python codesearch symbol Qualified.name --repo path/to/project --json` for
known symbols, or `python codesearch search "description" --repo path/to/project
--json` for discovery. Read the current source before changing behavior.

Keep parsing separate from discovery and ranking. Python has a standard-library
AST adapter; TS/JS has an optional TypeScript adapter; other UTF-8 files have an
explicit text-window fallback. Do not claim full semantic support for every
language or proven runtime call paths.

Source databases, embeddings, logs and private evaluation reports belong in the
per-user cache, never in committed fixtures. Use only original synthetic examples
or suitably licensed public data for shared evaluations. Do not add personal
paths, business-specific defaults, private excerpts or unpublished benchmark
claims to this repository.

Run `python -m unittest test_codesearch test_portability`. With optional
dependencies installed, also run `npm test` and the embedding tests via the virtual
environment's Python. Run `python evaluate.py --lexical-only` against the bundled
synthetic dataset. Preserve the dataset seal; new labels require a new revision.
