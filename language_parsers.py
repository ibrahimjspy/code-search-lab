"""Language adapters return source spans; retrieval has no framework knowledge.

Python uses its standard AST. TS/JS uses an optional Node parser. Every other
UTF-8 source file is searchable through explicit text windows.
"""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from paths import APP

JS_EXTENSIONS = {'.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.mts', '.cts'}
DOCUMENT_EXTENSIONS = {'.md', '.mdx', '.rst', '.txt', '.adoc'}


def javascript_available():
    return bool(shutil.which('node') and (APP / 'node_modules/typescript/package.json').is_file())


def parser_signature():
    files = [APP / 'extractor.cjs', Path(__file__)]
    content = b''.join(p.read_bytes() for p in files)
    package = APP / 'node_modules/typescript/package.json'
    if javascript_available():
        content += package.read_bytes()
    return hashlib.sha256(content + str(sys.version_info[:2]).encode()).hexdigest()


def text_records(text, suffix):
    return [dict(symbol='file', qualified_symbol='file',
                 kind='document' if suffix in DOCUMENT_EXTENSIONS else 'window',
                 start=1, end=max(1, len(text.splitlines())), declaration_start=1,
                 body_start=None, body_end=None, text=text, extractor='text-window-v1')]


def python_records(text):
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    records = []
    def add(node, name, qualified, kind, end=None):
        start = min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])])
        # Adjacent own-line comments attach to the declaration, not its sibling.
        while start > 1 and lines[start - 2].lstrip().startswith('#'):
            start -= 1
        finish = end if end is not None else node.end_lineno
        if finish < start:
            return
        records.append(dict(symbol=name, qualified_symbol=qualified, kind=kind,
                            start=start, end=finish, declaration_start=node.lineno,
                            body_start=node.lineno if kind in {'method','function'} else None,
                            body_end=node.end_lineno if kind in {'method','function'} else None,
                            text=''.join(lines[start - 1:finish]), extractor='python-ast-v1'))
    def visit(nodes, parent=''):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
                add(node, name, parent + '.' + name if parent else name, 'method' if parent else 'function')
            elif isinstance(node, ast.ClassDef):
                qualified = parent + '.' + node.name if parent else node.name
                first = node.body[0]
                boundary = min([first.lineno] + [d.lineno for d in getattr(first,'decorator_list',[])])
                while boundary > node.lineno and lines[boundary-2].lstrip().startswith('#'):
                    boundary -= 1
                add(node, node.name, qualified, 'class', boundary-1)
                visit(node.body, qualified)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                target = node.targets[0] if isinstance(node, ast.Assign) else node.target
                name = ast.unparse(target)
                add(node, name, parent + '.' + name if parent else name, 'variable')
    visit(tree.body)
    return records or text_records(text, '.py')


def parse_files(changed):
    result = {'files': {}, 'diagnostics': [], 'version': parser_signature()}
    js = []
    use_js = javascript_available()
    for name, text, _ in changed:
        suffix = Path(name).suffix.lower()
        if suffix in JS_EXTENSIONS and use_js:
            js.append({'path': name, 'text': text})
        elif suffix == '.py':
            try:
                result['files'][name] = python_records(text)
            except SyntaxError as exc:
                result['diagnostics'].append({'path':name,'line':exc.lineno or 1,'message':exc.msg})
        else:
            result['files'][name] = text_records(text, suffix)
    if js:
        process = subprocess.run(['node', str(APP / 'extractor.cjs')], input=json.dumps({'files': js}),
                                 capture_output=True, text=True, encoding='utf-8')
        if process.returncode:
            raise RuntimeError('JavaScript parser failed: ' + process.stderr[-1500:])
        parsed = json.loads(process.stdout)
        for name, records in parsed['files'].items():
            original = next(f['text'] for f in js if f['path'] == name)
            result['files'][name] = ([{**r, 'extractor': parsed['version']} for r in records]
                                     or text_records(original, Path(name).suffix))
        result['diagnostics'].extend(parsed['diagnostics'])
    return result
