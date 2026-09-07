# Code-intelligence layer

The CLI and MCP adapter share one local service per source directory. The service
owns the SQLite connection, source generations, navigation snapshot and optional
embedding model. Source content and editor context never go to a hosted model.

```mermaid
flowchart LR
    cli[CLI] --> service[Authenticated loopback service]
    mcp[MCP stdio adapter] --> service
    editor[Optional editor adapter] --> cli
    watcher[File events] --> index[Incremental index]
    index --> service
    service --> router[Query router]
    router --> lexical[Symbols / BM25 / regex]
    router --> neural[Resident model and vectors]
    router --> graph[Definitions / references / imports / calls]
```

## Start and stop

```sh
python setup.py --typescript --service
python codesearch start --repo path/to/project
python codesearch status --repo path/to/project
python codesearch search "description" --repo path/to/project --require-service --json
python codesearch stop --repo path/to/project
```

For semantic retrieval, also run `python setup.py --embeddings`, then start with
`--warm-model` or issue a `--mode dense` / `--mode hybrid` query. The encoder remains
loaded. Source changes invalidate candidate matrices, while unchanged document
vectors remain cached. A later rebuild reuses the encoder, not a second model.

Ordinary CLI queries automatically use a running service. `--local` bypasses it;
`--db` also selects standalone operation. Without a service, standalone searches
retain their full freshness scan. `--require-service` rejects that fallback.

The service binds only to loopback and authenticates requests with a random token
stored in the user cache. Browser Origin requests are rejected. A file lock prevents
two services from owning the same repository. Status output omits the token. This
is a single-user local service, not an Internet-facing or multi-tenant API.

## Freshness contract

Native file events are batched into changed paths. Incremental indexing reads only
those paths and removes deleted records. Ignore-rule and Git metadata changes
trigger reconciliation; a periodic full scan also repairs missed notifications.
Normal warm queries do not enumerate or hash the repository.

Before a query, the service waits for file events it has already observed. Native
event delivery is asynchronous: this is not a guarantee that every filesystem write
completed just before a request has already arrived at the watcher. Use:

- `--sync-path path/to/changed.py` to synchronously reconcile known saved files.
- `--full-sync` for a query with a complete reconciliation barrier.
- `python codesearch sync --repo path/to/project` to force full synchronization.

Responses identify the indexed generation and freshness policy. Parse errors keep
the previous good index but cause queries to fail visibly until the source is fixed.
There is no silent use of a known-broken generation.

## Routing and navigation

```sh
python codesearch search "Cache.get" --repo path/to/project --json
python codesearch search "re: return\\s+42" --repo path/to/project --json
python codesearch references Cache.get --repo path/to/project --json
python codesearch calls Cache.get --repo path/to/project --json
python codesearch callers Cache.get --repo path/to/project --json
python codesearch imports src/cache.py --repo path/to/project --json
python codesearch definitions --file src/main.ts --line 12 --column 8 --repo path/to/project --json
```

`auto` recognizes explicit `re:`, `regex:`, `def:`, `refs:`, `imports:`, `calls:` and
`callers:` prefixes. Otherwise it prefers an exact declaration name; natural-language
requests use BM25 until a model is resident, then hybrid retrieval. Explicit modes
override the router. The chosen mode and reason are returned. Regex execution has
per-match and overall time budgets.

Navigation uses a saved-source snapshot. TS/JS definitions and references use the
TypeScript checker, including import aliases and project compiler options. Python
navigation resolves a conservative subset of lexical/module bindings and method
names. Shadowed and dynamic calls remain unresolved instead of being guessed from
matching names. Imports and calls carry confidence labels. Compile-time resolution
does not prove which runtime implementation executes, especially with dependency
injection, monkey-patching, events or queues.

Source lines are one-based. Navigation columns are zero-based UTF-16 positions.
Other languages retain text retrieval and have no structured graph adapter yet.
The graph is rebuilt lazily after source changes; large projects may have a noticeable
first-navigation cost. It is not a complete interprocedural program analysis.

## Editor context

Pass `--context context.json`, or supply `context` to the MCP search tool:

```json
{
  "active_file": "src/cache.py",
  "selection": {"start_line": 10, "end_line": 15},
  "diagnostics": [{"file": "src/cache.py", "line": 12, "severity": "warning", "message": "Example diagnostic"}],
  "include_git_diff": true
}
```

A selection may instead contain a bounded `text` value from an unsaved buffer.
That text is labeled client-provided and does not overwrite the source index.
Diagnostics are supplied by the editor; this layer does not run linters or compilers
to generate diagnostics. Git context contains bounded tracked changes against HEAD
(staged changes if there is no HEAD). External diff drivers are disabled.

Context is request-local, returned alongside evidence, and never persisted as query
history. Active-file proximity breaks equal-score ties. When no query is supplied
to the API, selection text can supply it. The current index and graph still describe
saved files, not a full unsaved-buffer overlay.

`integrations/vscode` contains an optional VS Code-compatible adapter that captures
the active file, selection and diagnostics, invokes the CLI, and opens a selected
result. Other editors can implement the same JSON contract.

## MCP

Configure an MCP-capable client with a stdio command:

```json
{
  "command": "python",
  "args": ["/path/to/code-search-lab/codesearch", "mcp", "--repo", "/path/to/project"]
}
```

Use `python.exe` or an absolute Python executable where appropriate. The adapter
uses the supported MCP Python SDK 1.x maintenance line, pinned in service requirements.
Tools: `search_code`, `navigate_code`, `read_source`, and `index_status`.
The adapter starts or reuses the shared service; ending an MCP session does not stop
that service. Use the explicit stop command when finished.

## Validation and boundaries

Tests cover native watch updates, rename/ignore handling, periodic recovery,
incremental retention, static alias resolution, shadowing, model reuse, HTTP
authentication, path confinement and a real SDK MCP handshake/tool call.
Editor-context capture has unit coverage; full extension-host UI testing is separate.

This layer exposes read-only intelligence. Autonomous agent loops, patches, model-
specific editing, lint/test repair loops, per-request cancellation and comprehensive
audit trails are outside this implementation. Stopping the service is supported;
it does not provide a general job-cancellation protocol for long inference requests.
