const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {capture, relative} = require('./integrations/vscode/context.cjs');
test('editor capture uses relative paths, one-based lines and bounded diagnostics', () => {
  const root = path.resolve('sample');
  const uri = {scheme: 'file', fsPath: path.join(root,'source.py')};
  const vscode = {window: {activeTextEditor: {document: {uri, getText: () => 'selected source'},
    selection: {start: {line: 2}, end: {line: 4}}}},
    languages: {getDiagnostics: () => [{message:'example',severity:0,range:{start:{line:3,character:2}}}]}};
  const context = capture(vscode, root, true);
  assert.equal(context.active_file,'source.py');
  assert.equal(context.selection.start_line,3);
  assert.equal(context.diagnostics[0].line,4);
  assert.equal(context.include_git_diff,true);
  assert.equal(relative(root,path.resolve('outside.py')),null);
});
