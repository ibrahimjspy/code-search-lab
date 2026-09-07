// Compile-time navigation over the indexed TS/JS snapshot. No code is executed.
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

function navigate(root, files) {
  const normalize = p => path.resolve(p).replace(/\\/g, '/');
  const snapshots = new Map(files.map(f => [normalize(path.join(root, f.path)), f.text]));
  let options = {allowJs: true, checkJs: true, target: ts.ScriptTarget.Latest,
    module: ts.ModuleKind.NodeNext, moduleResolution: ts.ModuleResolutionKind.NodeNext, noEmit: true, skipLibCheck: true};
  const configFile = path.join(root, 'tsconfig.json');
  if (fs.existsSync(configFile)) {
    const loaded = ts.readConfigFile(configFile, ts.sys.readFile);
    if (!loaded.error) options = {...options, ...ts.parseJsonConfigFileContent(loaded.config, ts.sys, root).options, noEmit: true};
  }
  const host = ts.createCompilerHost(options);
  const read = host.readFile.bind(host);
  const exists = host.fileExists.bind(host);
  host.readFile = p => snapshots.has(normalize(p)) ? snapshots.get(normalize(p)) : read(p);
  host.fileExists = p => snapshots.has(normalize(p)) || exists(p);
  const program = ts.createProgram([...snapshots.keys()], options, host);
  const checker = program.getTypeChecker();
  const definitions = new Map(), occurrences = [], calls = [], imports = [];
  function location(node) {
    const source = node.getSourceFile();
    const start = source.getLineAndCharacterOfPosition(node.getStart(source));
    const end = source.getLineAndCharacterOfPosition(node.end);
    return {file: path.relative(root, source.fileName).replace(/\\/g, '/'), line: start.line + 1,
      column: start.character, end_line: end.line + 1, end_column: end.character};
  }
  function qualified(node) {
    const names = [];
    for (let current = node; current && !ts.isSourceFile(current); current = current.parent) {
      if (current.name && (ts.isDeclarationStatement(current) || ts.isMethodDeclaration(current)
          || ts.isVariableDeclaration(current) || ts.isParameter(current) || ts.isPropertyDeclaration(current)
          || ts.isGetAccessorDeclaration(current) || ts.isSetAccessorDeclaration(current))) {
        names.unshift(current.name.getText().replace(/^['"]|['"]$/g, ''));
      }
      if (ts.isConstructorDeclaration(current)) names.unshift('constructor');
    }
    return names.join('.') || 'module';
  }
  function definition(node) {
    if (!node || !snapshots.has(normalize(node.getSourceFile().fileName))) return null;
    const loc = location(node), id = `${loc.file}:${node.getStart()}`;
    if (!definitions.has(id)) definitions.set(id, {id, ...loc,
      symbol: node.name ? node.name.getText().replace(/^['"]|['"]$/g, '') : 'constructor',
      qualified_symbol: qualified(node), kind: ts.SyntaxKind[node.kind], confidence: 'compiler'});
    return id;
  }
  function targets(node) {
    let symbol = checker.getSymbolAtLocation(node);
    if (symbol && symbol.flags & ts.SymbolFlags.Alias) {
      try { symbol = checker.getAliasedSymbol(symbol); } catch { return []; }
    }
    return [...new Set((symbol?.declarations || []).map(definition).filter(Boolean))];
  }
  for (const source of program.getSourceFiles()) {
    if (!snapshots.has(normalize(source.fileName))) continue;
    function visit(node, owner = null) {
      if ((ts.isArrowFunction(node) || ts.isFunctionExpression(node)) && ts.isVariableDeclaration(node.parent))
        owner = definition(node.parent);
      if (ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node) || ts.isConstructorDeclaration(node)
          || ts.isGetAccessorDeclaration(node) || ts.isSetAccessorDeclaration(node)) owner = definition(node);
      if (node.name && (ts.isDeclarationStatement(node) || ts.isVariableDeclaration(node)
          || ts.isParameter(node) || ts.isPropertyDeclaration(node))) definition(node);
      if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node) && node.moduleSpecifier) {
        const specifier = node.moduleSpecifier.text;
        const resolved = ts.resolveModuleName(specifier, source.fileName, options, host).resolvedModule;
        imports.push({...location(node), module: specifier,
          target_file: resolved && snapshots.has(normalize(resolved.resolvedFileName))
            ? path.relative(root, resolved.resolvedFileName).replace(/\\/g, '/') : null,
          confidence: resolved ? 'compiler' : 'unresolved'});
      }
      if (ts.isIdentifier(node)) {
        const isDeclaration = node.parent.name === node && !ts.isPropertyAccessExpression(node.parent);
        if (!isDeclaration) occurrences.push({...location(node), text: node.text, targets: targets(node), confidence: 'compiler'});
      }
      if (ts.isCallExpression(node) || ts.isNewExpression(node)) {
        const expression = node.expression;
        const nameNode = ts.isPropertyAccessExpression(expression) ? expression.name : expression;
        const resolved = targets(nameNode);
        calls.push({...location(node), source: owner, expression: expression.getText(), targets: resolved,
          confidence: resolved.length ? 'compiler' : 'external_or_dynamic'});
      }
      ts.forEachChild(node, child => visit(child, owner));
    }
    visit(source);
  }
  return {definitions: [...definitions.values()], occurrences, calls, imports};
}
if (require.main === module) {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  process.stdout.write(JSON.stringify(navigate(input.root, input.files)));
}
module.exports = {navigate};
