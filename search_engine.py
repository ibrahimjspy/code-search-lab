"""Shared local retrieval: compiler sections, lexical ranking and optional vectors."""
import hashlib
import fnmatch
import json
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from paths import APP as LAB, source_root, default_db, cache_home
from language_parsers import parse_files, parser_signature

SKIP_PARTS = {'node_modules', '.git', '.hg', '.svn', '.index', '.venv', 'venv',
              '__pycache__', 'dist', 'build', 'coverage', 'vendor', '.pytest_cache'}
SKIP_NAMES = {'.gitignore', '.codesearchignore', 'package-lock.json', 'yarn.lock',
              'pnpm-lock.yaml', 'poetry.lock', 'uv.lock', 'Cargo.lock'}
STOP = set('a an the i we you how where what which is are was be to from in of for '
           'and or with do does can my our it this that find way code please'.split())
CANDIDATES = 50
RRF_K = 60


def words(text):
    text = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', text)
    text = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', text)
    return re.findall(r'[^\W_]+', text.casefold())


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def connect(db=None):
    db = Path(db) if db is not None else default_db(source_root())
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db, timeout=60)
    con.row_factory = sqlite3.Row
    con.executescript('''
      CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
      CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, digest TEXT);
      CREATE TABLE IF NOT EXISTS records (
        id INTEGER PRIMARY KEY, file TEXT, symbol TEXT, qualified_symbol TEXT,
        kind TEXT, start INTEGER, end INTEGER, declaration_start INTEGER,
        body_start INTEGER, body_end INTEGER, raw TEXT, content_hash TEXT,
        extractor TEXT);
      CREATE INDEX IF NOT EXISTS record_file ON records(file);
      CREATE INDEX IF NOT EXISTS record_symbol ON records(symbol);
      CREATE INDEX IF NOT EXISTS record_qualified ON records(qualified_symbol);
      CREATE VIRTUAL TABLE IF NOT EXISTS postings USING fts5(
        path_terms, symbol_terms, body_terms, tokenize='porter unicode61');
    ''')
    return con


def discover(root):
    root = source_root(root)
    patterns = []
    ignore = root / '.codesearchignore'
    if ignore.is_file():
        patterns = [line.strip() for line in ignore.read_text().splitlines()
                    if line.strip() and not line.lstrip().startswith('#')]
    names = None
    if shutil.which('git'):
        result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', '--cached',
                                 '--others', '--exclude-standard'], capture_output=True)
        if result.returncode == 0:
            names = set(result.stdout.decode().split('\0')) - {''}
    if names is None:
        names = set()
        for parent, directories, files in os.walk(root, followlinks=False):
            directories[:] = sorted(d for d in directories if d not in SKIP_PARTS
                                     and not (Path(parent) / d).is_symlink())
            names.update((Path(parent) / name).relative_to(root).as_posix() for name in files)
    for name in sorted(names):
        p = Path(name)
        if SKIP_PARTS.intersection(p.parts) or p.name in SKIP_NAMES:
            continue
        if any(part.startswith('.env') for part in p.parts) or p.suffix.lower() in {'.pem', '.key'}:
            continue
        if any(fnmatch.fnmatchcase(name, pattern) or fnmatch.fnmatchcase(p.name, pattern)
               or name.startswith(pattern.rstrip('/') + '/') for pattern in patterns):
            continue
        full = root / p
        if full.is_symlink() or not full.is_file() or full.stat().st_size > 2_000_000:
            continue
        if full.resolve().is_relative_to(cache_home().resolve()):
            continue
        data = full.read_bytes()
        if b'\0' in data:
            continue
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            continue
        if data and sum(byte < 32 and byte not in (9, 10, 13) for byte in data) / len(data) > .01:
            continue
        yield name, text, hashlib.sha256(data).hexdigest()


def bounded(record):
    """Same bounded source for lexical and dense retrieval; preserve parent identity."""
    pending, chars, start = [], 0, record['start']
    last_line = start
    for current_line, line in enumerate(record['text'].splitlines(keepends=True), record['start']):
        for offset in range(0, len(line), 1800):
            piece = line[offset:offset + 1800]
            if pending and (chars + len(piece) > 1800 or current_line - start >= 40):
                yield {**record, 'start': start, 'end': last_line, 'raw': ''.join(pending).rstrip()}
                pending, chars, start = [], 0, current_line
            pending.append(piece)
            chars += len(piece)
            last_line = current_line
    if pending and ''.join(pending).strip():
        yield {**record, 'start': start, 'end': last_line, 'raw': ''.join(pending).rstrip()}


def refresh(con, root=None):
    root = source_root(root)
    bound = con.execute("SELECT value FROM meta WHERE key='root'").fetchone()
    if bound and bound[0] != str(root):
        raise ValueError('Index belongs to another checkout; choose a different --db.')
    version = sha(parser_signature() + Path(__file__).read_text())
    previous = con.execute("SELECT value FROM meta WHERE key='format'").fetchone()
    rebuild = previous is None or previous[0] != version
    old = {} if rebuild else dict(con.execute('SELECT path, digest FROM files'))
    current = list(discover(root))
    changed = [(name, text, digest) for name, text, digest in current if old.get(name) != digest]
    seen = {name for name, _, _ in current}
    parsed = parse_files(changed)  # A parser failure preserves the last good index.
    if parsed['diagnostics']:
        preview = '\n'.join(f"{d['path']}:{d['line']}: {d['message']}" for d in parsed['diagnostics'][:5])
        raise RuntimeError('Source parse errors; previous index preserved.\n' + preview)
    deleted = set(old) - seen
    with con:
        if rebuild:
            con.execute('DELETE FROM postings')
            con.execute('DELETE FROM records')
            con.execute('DELETE FROM files')
        con.execute("INSERT OR REPLACE INTO meta VALUES ('root', ?)", (str(root),))
        con.execute("INSERT OR REPLACE INTO meta VALUES ('format', ?)", (version,))
        for name in {name for name, _, _ in changed} | deleted:
            con.execute('DELETE FROM postings WHERE rowid IN (SELECT id FROM records WHERE file=?)', (name,))
            con.execute('DELETE FROM records WHERE file=?', (name,))
            con.execute('DELETE FROM files WHERE path=?', (name,))
        for name, text, digest in changed:
            extracted = parsed['files'].get(name, [])
            for record in extracted:
                for piece in bounded(record):
                    payload = name + '\n' + piece['qualified_symbol'] + '\n' + piece['raw']
                    cursor = con.execute('''INSERT INTO records
                      (file,symbol,qualified_symbol,kind,start,end,declaration_start,body_start,body_end,raw,content_hash,extractor)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (name, piece['symbol'], piece['qualified_symbol'], piece['kind'], piece['start'], piece['end'],
                       piece.get('declaration_start', record['start']), piece.get('body_start'), piece.get('body_end'),
                       piece['raw'], sha(payload), piece['extractor']))
                    con.execute('INSERT INTO postings(rowid,path_terms,symbol_terms,body_terms) VALUES (?,?,?,?)',
                                (cursor.lastrowid, ' '.join(words(name)), ' '.join(words(piece['qualified_symbol'])),
                                 ' '.join(words(piece['raw']))))
            con.execute('INSERT OR REPLACE INTO files VALUES (?,?)', (name, digest))
    return {'files': len(seen), 'updated': len(changed), 'deleted': len(deleted),
            'chunks': con.execute('SELECT count(*) FROM records').fetchone()[0],
            'extractors': [r[0] for r in con.execute('SELECT DISTINCT extractor FROM records ORDER BY extractor')],
            'rebuilt': rebuild}


def eligible(con, path_filter=None, include_docs=False):
    return [dict(r) for r in con.execute('''SELECT * FROM records WHERE
        (? IS NULL OR substr(file,1,length(?))=?) AND (? OR kind!='document') ORDER BY id''',
        (path_filter, path_filter, path_filter, include_docs))]


def exact_candidates(con, query, path_filter=None, include_docs=False):
    symbol = query.strip().removesuffix('()')
    return [{**dict(r), 'score': 1.0, 'matched_by': 'exact_symbol'} for r in con.execute('''
      SELECT * FROM records WHERE (symbol=? OR qualified_symbol=?) AND
      (? IS NULL OR substr(file,1,length(?))=?) AND (? OR kind!='document')
      ORDER BY (body_start IS NULL),file,start,id''',
      (symbol, symbol, path_filter, path_filter, path_filter, include_docs))]


def bm25_candidates(con, query, path_filter=None, include_docs=False, limit=CANDIDATES):
    terms = list(dict.fromkeys(w for w in words(query) if w not in STOP))
    if not terms:
        return []
    expression = ' OR '.join('"' + t + '"' for t in terms)
    rows = [dict(r) for r in con.execute('''SELECT records.*, bm25(postings,3.0,5.0,1.0) AS lexical_score
       FROM postings JOIN records ON postings.rowid=records.id WHERE postings MATCH ? AND
       (? IS NULL OR substr(file,1,length(?))=?) AND (? OR kind!='document')
       ORDER BY lexical_score, records.id LIMIT 150''',
       (expression, path_filter, path_filter, path_filter, include_docs))]
    for row in rows:
        path_words = ' ' + ' '.join(words(row['file'])) + ' '
        path_match = any(f' {a} {b} ' in path_words for a, b in zip(terms, terms[1:]))
        coverage = len(set(terms) & set(words(row['file'] + ' ' + row['symbol'] + ' ' + row['raw']))) / len(terms)
        row['score'] = -row.pop('lexical_score') * (2.0 if path_match else 1.0) * (0.5 + coverage)
        row['matched_by'] = 'bm25'
    return sorted(rows, key=lambda r: (-r['score'], r['id']))[:limit]


def fuse(lists, k=RRF_K):
    fused = {}
    for label, candidates in lists:
        for rank, candidate in enumerate(candidates, 1):
            row = fused.setdefault(candidate['id'], {**candidate, 'score': 0.0,
                                                     'matched_by': 'rrf', 'ranks': {}})
            row['score'] += 1.0 / (k + rank)
            row['ranks'][label] = rank
    return sorted(fused.values(), key=lambda r: (-r['score'], r['id']))


def select(candidates, limit):
    """Deduplicate method windows rather than arbitrarily cap whole files at two."""
    selected, seen = [], set()
    for row in candidates:
        key = (row['file'], row['qualified_symbol'])
        if row['kind'] in {'window', 'document', 'statement'}:
            key += (row['start'],)
        if key in seen:
            continue
        seen.add(key)
        selected.append(row)
        if len(selected) == limit:
            break
    return selected


def present(row, query):
    terms = set(words(query)) - STOP
    lines = row['raw'].splitlines()
    best = max(range(len(lines)), key=lambda i: len(set(words(lines[i])) & terms)) if lines else 0
    offset = max(0, best - 3)
    return {key: row[key] for key in ('id','file','symbol','qualified_symbol','kind','start','end','score','matched_by')} | {
        'preview_start': row['start'] + offset, 'preview': '\n'.join(lines[offset:offset + 12]),
        'content_hash': row['content_hash'], 'ranks': row.get('ranks', {})}


def search(con, query, limit=5, mode='bm25', path_filter=None, include_docs=False, dense=None):
    if mode == 'symbol':
        candidates = exact_candidates(con, query, path_filter, include_docs)
    elif mode == 'bm25':
        candidates = bm25_candidates(con, query, path_filter, include_docs)
    else:
        if dense is None:
            from semantic import DenseIndex
            dense = DenseIndex(con, path_filter=path_filter, include_docs=include_docs)
        semantic = dense.candidates(query, CANDIDATES)
        candidates = semantic if mode == 'dense' else fuse([
            ('bm25', bm25_candidates(con, query, path_filter, include_docs)), ('dense', semantic)])
    return [present(row, query) for row in select(candidates, limit)]
