"""Static navigation with explicit confidence, independent of retrieval ranking."""
import ast
import json
from pathlib import Path, PurePosixPath
import subprocess
from language_parsers import JS_EXTENSIONS, javascript_available
from paths import APP


def python_graph(sources):
    graph = {k: [] for k in ['definitions', 'occurrences', 'calls', 'imports']}
    trees, scopes, module_defs = {}, {}, {}
    def loc(file, node):
        lines = sources[file].splitlines()
        def col(line, byte):
            prefix = lines[line-1].encode('utf-8')[:byte].decode('utf-8')
            return len(prefix.encode('utf-16-le')) // 2
        return dict(file=file, line=node.lineno, column=col(node.lineno,node.col_offset),
                    end_line=node.end_lineno, end_column=col(node.end_lineno,node.end_col_offset))
    for file, text in sources.items():
        if not file.endswith('.py'):
            continue
        tree = trees[file] = ast.parse(text)
        module_defs[file] = {}
        def collect(nodes, parent=''):
            for node in nodes:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name = parent + '.' + node.name if parent else node.name
                    identity = file + '#' + name
                    definition = dict(id=identity, **loc(file,node), symbol=node.name,
                                      qualified_symbol=name, kind=type(node).__name__, confidence='scope')
                    graph['definitions'].append(definition)
                    scopes[(file,name)] = definition
                    if not parent: module_defs[file][node.name] = identity
                    if isinstance(node, ast.ClassDef): collect(node.body, name)
        collect(tree.body)
    def module_file(file, module, level):
        parts = list(PurePosixPath(file).parent.parts) if level else []
        if level > 1: parts = parts[:max(0,len(parts)-level+1)]
        parts += module.split('.') if module else []
        path = '/'.join(parts)
        return next((p for p in [path+'.py',path+'/__init__.py'] if p in trees), None)
    for file, tree in trees.items():
        imported = {}
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    module = node.module if isinstance(node, ast.ImportFrom) else alias.name
                    target = module_file(file,module or '',getattr(node,'level',0))
                    graph['imports'].append(dict(**loc(file,node),module=module,
                        target_file=target, confidence='scope' if target else 'unresolved'))
                    imported[alias.asname or alias.name.split('.')[0]] = (target,
                        alias.name if isinstance(node,ast.ImportFrom) else None)
        def walk(node, owner=None, class_name=None, shadows=frozenset()):
            if isinstance(node, ast.ClassDef):
                for child in node.body: walk(child, None, node.name)
                return
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = class_name+'.'+node.name if class_name else node.name
                owner = file+'#'+qualified
                arguments = [*node.args.posonlyargs,*node.args.args,*node.args.kwonlyargs]
                if node.args.vararg: arguments.append(node.args.vararg)
                if node.args.kwarg: arguments.append(node.args.kwarg)
                shadows = {a.arg for a in arguments} | {n.id for n in ast.walk(node)
                    if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Store)}
                for child in node.body: walk(child,owner,class_name,shadows)
                return
            def resolve(expression):
                if isinstance(expression,ast.Name):
                    if expression.id in shadows: return []
                    if expression.id in imported:
                        target, name = imported[expression.id]
                        value = module_defs.get(target,{}).get(name)
                    else: value = module_defs[file].get(expression.id)
                    return [value] if value else []
                if isinstance(expression,ast.Attribute) and isinstance(expression.value,ast.Name):
                    receiver = expression.value.id
                    if receiver in {'self','cls'} and class_name:
                        definition = scopes.get((file,class_name+'.'+expression.attr))
                        return [definition['id']] if definition else []
                    if receiver in imported and receiver not in shadows:
                        target, imported_name = imported[receiver]
                        qualified = imported_name+'.'+expression.attr if imported_name else expression.attr
                        definition = scopes.get((target,qualified))
                        return [definition['id']] if definition else []
                return []
            if isinstance(node,ast.Call):
                targets = resolve(node.func)
                graph['calls'].append(dict(**loc(file,node),source=owner, expression=ast.unparse(node.func),
                    targets=targets, confidence='scope' if targets else 'external_or_dynamic'))
            if isinstance(node,(ast.Name,ast.Attribute)) and isinstance(node.ctx,ast.Load):
                targets = resolve(node)
                graph['occurrences'].append(dict(**loc(file,node),text=ast.unparse(node),targets=targets,
                    confidence='scope' if targets else 'unresolved'))
            for child in ast.iter_child_nodes(node): walk(child,owner,class_name,shadows)
        for node in tree.body: walk(node)
    return graph


class Navigation:
    def __init__(self, root, con):
        sources = {r['path']:r['text'] for r in con.execute('SELECT path,text FROM files') if r['text'] is not None}
        self.graph = python_graph(sources)
        js = [{'path':p,'text':t} for p,t in sources.items() if Path(p).suffix.lower() in JS_EXTENSIONS]
        if js and javascript_available():
            result = subprocess.run(['node',str(APP/'navigation.cjs')], input=json.dumps({'root':str(root),'files':js}),
                                    capture_output=True,text=True,encoding='utf-8',timeout=60)
            if result.returncode: raise RuntimeError('Navigation compiler failed: '+result.stderr[-1000:])
            for key, values in json.loads(result.stdout).items(): self.graph[key].extend(values)
        self.by_id = {d['id']:d for d in self.graph['definitions']}

    def definitions(self, query='', file=None, line=None, column=0):
        if file and line is not None:
            hits = [o for o in self.graph['occurrences'] if o['file']==file
                    and (o['line'],o['column']) <= (line,column) < (o['end_line'],o['end_column'])]
            if hits:
                chosen = min(hits,key=lambda o:(o['end_line']-o['line'],o['end_column']-o['column']))
                return [self.by_id[i] for i in chosen['targets'] if i in self.by_id]
            declarations = [d for d in self.graph['definitions'] if d['file']==file
                            and (d['line'],d['column']) <= (line,column) < (d['end_line'],d['end_column'])]
            if declarations:
                return [min(declarations,key=lambda d:(d['end_line']-d['line'],d['end_column']-d['column']))]
        symbol = query.strip().removesuffix('()')
        return [d for d in self.graph['definitions'] if (not symbol or symbol in {d['symbol'],d['qualified_symbol']})
                and (file is None or d['file']==file)]

    def query(self, mode, query='', file=None, line=None, column=0, limit=20):
        if mode=='imports':
            return [i for i in self.graph['imports'] if not (file or query) or i['file']==(file or query)][:limit]
        definitions = self.definitions(query,file,line,column)
        if mode=='definitions': return definitions[:limit]
        targets = {d['id'] for d in definitions}
        if mode=='references':
            return [o for o in self.graph['occurrences'] if targets.intersection(o['targets'])][:limit]
        if mode in {'calls','callers'}:
            edges = [c for c in self.graph['calls'] if
                     (c['source'] in targets if mode=='calls' else bool(targets.intersection(c['targets'])))]
            return [{**edge,'resolved_targets':[self.by_id[i] for i in edge['targets'] if i in self.by_id]}
                    for edge in edges[:limit]]
        raise ValueError('Unknown navigation mode')
