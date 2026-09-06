// Syntax is owned by TypeScript; this lab owns which source spans become records.
const fs = require('node:fs');
const ts = require('typescript');
const VERSION = `typescript-${ts.version}-v1`;

function extract(path, text) {
  const lowerPath = path.toLowerCase();
  const kind = lowerPath.endsWith('.tsx') ? ts.ScriptKind.TSX : lowerPath.endsWith('.jsx') ? ts.ScriptKind.JSX
    : /\.(js|mjs|cjs)$/.test(lowerPath) ? ts.ScriptKind.JS : ts.ScriptKind.TS;
  const source = ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true, kind);
  const records = [];
  const line = pos => source.getLineAndCharacterOfPosition(Math.max(0, pos)).line + 1;
  function leadingStart(node) {
    let start = node.getStart(source);
    // Own-line comments belong with this declaration; trailing comments do not.
    for (const comment of ts.getLeadingCommentRanges(text, node.getFullStart()) || []) {
      const lineStart = text.lastIndexOf('\n', comment.pos - 1) + 1;
      if (!text.slice(lineStart, comment.pos).trim()) start = Math.min(start, comment.pos);
    }
    return start;
  }
  function emit(node, symbol, qualified, nodeKind, endOverride) {
    const start = leadingStart(node);
    const end = endOverride === undefined ? node.end : endOverride;
    if (end <= start || !text.slice(start, end).trim()) return;
    records.push({symbol, qualified_symbol: qualified, kind: nodeKind,
      start: line(start), end: line(end - 1), declaration_start: line(node.getStart(source)),
      body_start: node.body ? line(node.body.getStart(source)) : null,
      body_end: node.body ? line(node.body.end - 1) : null,
      text: text.slice(start, end)});
  }
  function visit(node, parent = '') {
    const name = node.name ? node.name.getText(source).replace(/^['"]|['"]$/g, '') : 'default';
    const qualified = parent ? `${parent}.${name}` : name;
    if (ts.isClassDeclaration(node) || ts.isClassExpression(node)) {
      // The class header is useful context, but duplicating every method in a
      // second whole-class record would distort lexical and dense comparisons.
      const first = node.members[0];
      emit(node, name, qualified, 'class', first ? first.getFullStart() : node.end);
      for (const member of node.members) {
        const memberName = ts.isConstructorDeclaration(member) ? 'constructor'
          : member.name ? member.name.getText(source).replace(/^['"]|['"]$/g, '') : 'member';
        const memberKind = ts.isMethodDeclaration(member) ? 'method'
          : ts.isConstructorDeclaration(member) ? 'constructor'
          : ts.isGetAccessorDeclaration(member) ? 'getter'
          : ts.isSetAccessorDeclaration(member) ? 'setter' : 'property';
        emit(member, memberName, `${qualified}.${memberName}`, memberKind);
      }
    } else if (ts.isFunctionDeclaration(node)) {
      emit(node, name, qualified, 'function');
    } else if (ts.isVariableStatement(node)) {
      // Keep exported tool objects and arrow functions, without indexing locals
      // inside a method as competing top-level functions.
      const names = node.declarationList.declarations.map(d => d.name.getText(source));
      emit(node, names.join(','), parent ? `${parent}.${names.join(',')}` : names.join(','), 'variable');
    } else if (ts.isModuleDeclaration(node) && node.body) {
      if (ts.isModuleBlock(node.body)) for (const child of node.body.statements) visit(child, qualified);
      else visit(node.body, qualified);
    } else if (ts.isInterfaceDeclaration(node) || ts.isTypeAliasDeclaration(node) || ts.isEnumDeclaration(node)) {
      emit(node, name, qualified, ts.isInterfaceDeclaration(node) ? 'interface' : ts.isEnumDeclaration(node) ? 'enum' : 'type');
    } else if (ts.isImportDeclaration(node) || ts.isImportEqualsDeclaration(node)) {
      // Imports are covered by later compiler-reference work; they are noisy
      // standalone behavior-search candidates in this small experiment.
    } else if (node.getText(source).trim()) {
      emit(node, 'module', parent || 'module', 'statement');
    }
  }
  for (const statement of source.statements) visit(statement);
  return {records, diagnostics: source.parseDiagnostics.map(d => ({path,
    line: line(d.start || 0), message: ts.flattenDiagnosticMessageText(d.messageText, '\n')}))};
}

if (require.main === module) {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const files = {}, diagnostics = [];
  for (const file of input.files) {
    const result = extract(file.path, file.text);
    files[file.path] = result.records;
    diagnostics.push(...result.diagnostics);
  }
  process.stdout.write(JSON.stringify({version: VERSION, files, diagnostics}));
}
module.exports = {extract, VERSION};
