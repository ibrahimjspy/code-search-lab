const assert = require('node:assert/strict');
const {test} = require('node:test');
const {extract} = require('./extractor.cjs');

test('comments and decorators stay with their own methods', () => {
  const {records} = extract('a.ts', `class Service {\n  first() { return 1; }\n\n  /** Make a draft. */\n  @Post('draft')\n  async second<T>(\n    value: T\n  ) {\n    const local = value;\n    return local;\n  }\n}`);
  const first = records.find(r => r.symbol === 'first');
  const second = records.find(r => r.symbol === 'second');
  assert.equal(first.end, 2);
  assert.equal(second.start, 4);
  assert.equal(second.end, 11);
  assert.equal(second.qualified_symbol, 'Service.second');
  assert.ok(second.text.includes('@Post'));
  assert.ok(!first.text.includes('draft'));
  assert.ok(!records.some(r => r.symbol === 'local'));
  assert.ok(!records.find(r => r.kind === 'class').text.includes('return'));
});

test('qualified identity, arrows, CRLF and unicode source locations', () => {
  const text = '// café\r\nclass A { run() { return "é"; } }\r\nclass B { run() {} }\r\nexport const send = async () => 1;';
  const {records} = extract('b.ts', text);
  assert.ok(records.some(r => r.qualified_symbol === 'A.run' && r.start === 2));
  assert.ok(records.some(r => r.qualified_symbol === 'B.run' && r.start === 3));
  assert.equal(records.find(r => r.symbol === 'send').start, 4);
});

test('trailing comment is not attached to the next declaration', () => {
  const {records} = extract('a.ts', 'function first() {} // previous\n/** next */\nfunction second() {}');
  const second = records.find(r => r.symbol === 'second');
  assert.equal(second.start, 2);
  assert.ok(!second.text.includes('previous'));
});

test('parse errors are visible and interfaces are indexed', () => {
  assert.ok(extract('a.ts', 'class Broken { foo( {').diagnostics.length);
  assert.equal(extract('a.ts', 'interface Config { body: string; }').records[0].kind, 'interface');
});

test('uppercase JSX file extensions select the correct grammar', () => {
  const result = extract('Component.TSX', 'export function View() { return <div>Hello</div>; }');
  assert.equal(result.diagnostics.length, 0);
  assert.ok(result.records.some(r => r.symbol === 'View'));
});
