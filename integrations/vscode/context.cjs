const path = require('node:path');

function relative(root, file) {
  const value = path.relative(root, file);
  if (value === '..' || value.startsWith(`..${path.sep}`) || path.isAbsolute(value)) return null;
  return value.replace(/\\/g, '/');
}

function capture(vscode, root, includeGitDiff) {
  const editor = vscode.window.activeTextEditor;
  const context = {include_git_diff: includeGitDiff, diagnostics: []};
  if (editor && editor.document.uri.scheme === 'file') {
    const active = relative(root, editor.document.uri.fsPath);
    if (active !== null) {
      context.active_file = active;
      const selected = editor.document.getText(editor.selection);
      if (selected) context.selection = {text: selected.slice(0, 8000),
        start_line: editor.selection.start.line + 1, end_line: editor.selection.end.line + 1};
      context.diagnostics = vscode.languages.getDiagnostics(editor.document.uri).slice(0, 20).map(d => ({
        file: active, line: d.range.start.line + 1, column: d.range.start.character,
        message: d.message.slice(0, 1000), severity: ['error','warning','information','hint'][d.severity] || 'unknown'}));
    }
  }
  return context;
}
module.exports = {capture, relative};
