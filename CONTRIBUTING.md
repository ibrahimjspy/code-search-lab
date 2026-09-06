# Contributing

Keep the core independent of any application, framework, editor or agent. Add
language support through the parser adapter contract and preserve text fallback.
Use relative, generic examples and original synthetic fixtures in tests.

Do not commit source indexes, embeddings, private query logs, proprietary source
excerpts, personal paths or private benchmark reports. Generated evaluation output
belongs in the user cache unless intentionally exported from public fixtures.

Before submitting changes, run the Python tests, the optional TypeScript parser
tests when available, and the synthetic lexical evaluation. Report which operating
systems, languages and optional model configurations were actually exercised.

Separate correctness fixes from ranking experiments. Freeze a dataset before
tuning and compare retrieval modes on identical records. Distinguish cold startup,
indexing, warm retrieval and full agent-task latency when reporting performance.
