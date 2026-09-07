# Optional editor-context adapter

This unpublished extension connects a VS Code-compatible editor to the local CLI.
It captures the active file, selected text and diagnostics and lets you open a
retrieved source location. It requires a trusted workspace and executes only the
Python CLI configured by the user, without shell interpolation.

For local development, launch an extension development host:

```sh
code --extensionDevelopmentPath=/path/to/code-search-lab/integrations/vscode /path/to/project
```

Configure `codeSearchLab.cliPath` with the absolute path to `codesearch` and
`codeSearchLab.python` with your Python executable. Run **Code Search Lab: Start
Local Service**, then **Code Search Lab: Search with Editor Context**.

Optional settings select the retrieval mode, include a bounded Git diff, or warm
the model at startup. Unsaved selections are context only; they are not overlaid
onto the disk index. Temporary context files are removed after the command.

The capture contract is unit-tested. Manual validation inside an editor extension
host is still required before a marketplace release. This adapter is not required
for CLI or MCP usage and is not published to an extension marketplace.
