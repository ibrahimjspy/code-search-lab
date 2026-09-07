"""Shared code-intelligence operations over one coherent source snapshot."""
import threading
import time
from pathlib import Path
import search_engine as engine
from paths import source_root, default_db
from editor_context import collect, relative_path
from navigation import Navigation

MODES = {'auto','symbol','bm25','dense','hybrid','regex','definitions','references','imports','calls','callers'}


class Intelligence:
    def __init__(self, root, db=None, encoder_factory=None):
        self.root = source_root(root)
        self.con = engine.connect(db or default_db(self.root))
        self.lock = threading.RLock()
        self.encoder_factory = encoder_factory
        self.encoder = None
        self.dense = {}
        self.navigation = None
        self.generation = 0
        self.full_scans = 0
        self.model_loads = 0
        self.error = None
        self.stats = {}
        self.update()

    def update(self, paths=None):
        with self.lock:
            try:
                self.stats = engine.refresh(self.con,self.root,paths)
                self.full_scans += int(self.stats.get('full_scan',False))
                self.error = None
                if self.stats['updated'] or self.stats['deleted'] or self.stats['rebuilt']:
                    self.generation += 1
                    self.dense.clear()
                    self.navigation = None
                return self.stats
            except Exception as exc:
                self.error = str(exc)
                raise

    def status(self):
        return {'root':str(self.root),'generation':self.generation,'index':self.stats,
                'full_scans':self.full_scans,'model_loaded':self.encoder is not None,
                'model_loads':self.model_loads,'index_error':self.error}

    def dense_index(self, path_filter=None, include_docs=False):
        from semantic import CodeEncoder, DenseIndex
        if self.encoder is None:
            self.encoder = self.encoder_factory() if self.encoder_factory else CodeEncoder()
            self.model_loads += 1
        key = (path_filter,include_docs)
        if key not in self.dense:
            if len(self.dense)>=4: self.dense.pop(next(iter(self.dense)))
            self.dense[key] = DenseIndex(self.con,path_filter,include_docs,encoder=self.encoder)
        return self.dense[key]

    def graph(self):
        if self.navigation is None: self.navigation = Navigation(self.root,self.con)
        return self.navigation

    def read(self, file, start=1, end=None):
        file = relative_path(self.root,file)
        row = self.con.execute('SELECT text,digest FROM files WHERE path=?',(file,)).fetchone()
        if row is None: raise ValueError('File is not indexed or is excluded.')
        end = end or start+39
        if not isinstance(start,int) or not isinstance(end,int) or not 1<=start<=end<=start+199:
            raise ValueError('Read spans must contain at most 200 lines.')
        text = '\n'.join(row['text'].splitlines()[start-1:end])
        return {'file':file,'start':start,'end':end,'text':text[:32000],'truncated':len(text)>32000,
                'source_hash':row['digest'],'generation':self.generation}

    def query(self, query='', mode='auto', limit=5, path=None, context=None, file=None, line=None,
              column=0, include_docs=False, include_git_diff=False, **unused):
        if unused: raise ValueError('Unknown query options: '+', '.join(unused))
        if mode not in MODES or not isinstance(query,str) or len(query)>8000:
            raise ValueError('Invalid query or retrieval mode.')
        if not isinstance(limit,int) or not 1<=limit<=50: raise ValueError('Limit must be 1-50.')
        started = time.perf_counter()
        with self.lock:
            if self.error: raise RuntimeError(self.error)
            editor = collect(self.root,self.con,context,include_git_diff)
            if not query and editor['selection']:
                query = editor['selection']['text'][:2000]
            file = relative_path(self.root,file) if file else None
            path = relative_path(self.root,path) if path else None
            if path == '.': path = None
            reason = 'explicit mode'
            if mode=='auto':
                prefix,separator,body = query.partition(':')
                routes = {'re':'regex','regex':'regex','def':'definitions','refs':'references',
                          'imports':'imports','calls':'calls','callers':'callers'}
                if separator and prefix in routes: mode,query,reason = routes[prefix],body.strip(),'query prefix'
                elif any(r['kind'] not in {'window','document','statement'} for r in
                         engine.exact_candidates(self.con,query,path,include_docs)):
                    mode,reason = 'symbol','exact indexed symbol'
                else: mode,reason = ('hybrid','warm semantic model') if self.encoder is not None else ('bm25','lexical default')
            if mode in {'definitions','references','imports','calls','callers'}:
                results = self.graph().query(mode,query,file,line,column,limit)
            elif mode=='regex':
                import regex
                if len(query)>1000: raise ValueError('Regex is limited to 1000 characters.')
                pattern = regex.compile(query)
                results = []
                deadline = time.monotonic()+2
                for row in engine.eligible(self.con,path,include_docs):
                    if time.monotonic()>deadline: raise TimeoutError('Regex query time budget exceeded.')
                    match = pattern.search(row['raw'],timeout=.02)
                    if match:
                        row.update(score=1.0,matched_by='regex')
                        result = engine.present(row,query)
                        offset = row['raw'][:match.start()].count('\n')
                        result['preview_start'] = row['start']+offset
                        result['preview'] = '\n'.join(row['raw'].splitlines()[offset:offset+12])
                        results.append(result)
                        if len(results)==limit: break
            else:
                dense = self.dense_index(path,include_docs) if mode in {'dense','hybrid'} else None
                # Retrieve a shortlist before applying a small, explicit context tie-break.
                results = engine.search(self.con,query,min(50,limit*3),mode,path,include_docs,dense)
                if editor['active_file']:
                    # Do not replace relevance with proximity; only break equal-score ties.
                    results.sort(key=lambda r:(-r['score'],r['file']!=editor['active_file']))
                results = results[:limit]
            return {'route':{'mode':mode,'reason':reason},'results':results,'editor_context':editor,
                    'generation':self.generation,'elapsed_ms':round((time.perf_counter()-started)*1000,2),
                    'navigation_limits':'TS/JS: compile-time links; Python: lexical scope; dynamic targets may be unresolved'}

    def close(self):
        with self.lock: self.con.close()
