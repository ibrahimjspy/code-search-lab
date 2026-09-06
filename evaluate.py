#!/usr/bin/env python3
"""Frozen, source-checked retrieval evaluation. No tuning or model fitting here."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import time
from datetime import datetime, timezone
import search_engine as engine
from paths import source_root, default_db, repo_cache, venv_python, APP


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_labels(data, root):
    for path, expected in data['source_snapshot']['files'].items():
        if not (root / path).resolve().is_relative_to(root):
            raise ValueError('Dataset source path escapes its root.')
        if file_hash(root / path) != expected:
            raise ValueError(f'Frozen label source changed: {path}. Create a new dataset revision; do not silently reuse line labels.')
    for query in data['queries']:
        for target in query['expected']:
            if not target['start'] <= target['evidence_start'] <= target['evidence_end'] <= target['end']:
                raise ValueError(f"Invalid source span for {query['id']}")


def matches(row, target):
    return (row['file'] == target['file'] and row['symbol'] == target['symbol']
            and ('qualified_symbol' not in row or row['qualified_symbol'] == target.get('qualified_symbol', row['qualified_symbol']))
            and row['start'] <= target['end'] and row['end'] >= target['start'])


def score(rows, query):
    targets = query['expected']
    rank = next((i for i, r in enumerate(rows, 1) if any(matches(r, t) for t in targets)), None)
    evidence, covered = set(), set()
    for target in targets:
        required = {(target['file'], n) for n in range(target['evidence_start'], target['evidence_end'] + 1)}
        evidence |= required
        for row in rows[:5]:
            if matches(row, target):
                visible = {(row['file'], n) for n in range(row['preview_start'], row['preview_start'] + len(row['preview'].splitlines()))}
                covered |= required & visible
    return {'rank': rank, 'hit1': rank == 1, 'hit5': rank is not None and rank <= 5,
            'reciprocal_rank50': 0 if rank is None else 1 / rank,
            'target_recall50': sum(any(matches(row, t) for row in rows) for t in targets) / len(targets),
            'evidence_preview_coverage5': len(covered) / len(evidence) if evidence else 0}


def aggregate(results):
    summary = {}
    for split in ['development', 'evaluation', 'all']:
        subset = [r for r in results if split == 'all' or r['split'] == split]
        summary[split] = {}
        for mode in ['bm25', 'dense', 'hybrid']:
            rows = [r['modes'][mode] for r in subset if mode in r['modes']]
            if not rows:
                continue
            latencies = sorted(r['query_ms'] for r in rows)
            summary[split][mode] = {'questions': len(rows), 'hit1': sum(r['hit1'] for r in rows),
                'hit5': sum(r['hit5'] for r in rows),
                'mrr50': round(statistics.mean(r['reciprocal_rank50'] for r in rows), 4),
                'target_recall50': round(statistics.mean(r['target_recall50'] for r in rows), 4),
                'evidence_preview_coverage5': round(statistics.mean(r['evidence_preview_coverage5'] for r in rows), 4),
                'median_query_ms': round(statistics.median(latencies), 2),
                'p95_query_ms': round(latencies[max(0, int(len(latencies) * .95 + .9999) - 1)], 2),
                'mean_preview_chars5': round(statistics.mean(r['preview_chars5'] for r in rows))}
    return summary


def run(args):
    data = json.loads(args.dataset.read_text())
    root = source_root(args.repo if args.repo is not None else
                       args.dataset.parent / data.get('source_dir', '.'))
    args.db = args.db or default_db(root)
    args.output = args.output or repo_cache(root) / 'reports' / 'evaluation.json'
    verify_labels(data, root)
    frozen_hash = file_hash(args.dataset)
    seal = args.dataset.with_suffix('.sha256')
    if not seal.exists() or seal.read_text().strip() != frozen_hash:
        raise ValueError('Dataset seal missing or changed. Freeze a new revision before evaluating.')
    con = engine.connect(args.db)
    t = time.perf_counter()
    stats = engine.refresh(con, root)
    refresh_ms = (time.perf_counter() - t) * 1000
    source_manifest = dict(con.execute('SELECT path,digest FROM files'))
    record_fingerprint = engine.sha(json.dumps([tuple(r) for r in con.execute(
        'SELECT id,content_hash,start,end FROM records ORDER BY id')]))
    dense = None
    if not args.lexical_only:
        from semantic import DenseIndex
        dense = DenseIndex(con)
        dense.encoder.query('Locate the function that performs an operation.')  # Untimed model warmup.
    results = []
    for query in data['queries']:
        text = query['query']
        mode_results = {}
        t = time.perf_counter()
        lexical = engine.bm25_candidates(con, text)
        lexical_ms = (time.perf_counter() - t) * 1000
        candidates = {'bm25': (lexical, lexical_ms)}
        if dense is not None:
            t = time.perf_counter()
            semantic = dense.candidates(text)
            dense_ms = (time.perf_counter() - t) * 1000
            t = time.perf_counter()
            hybrid = engine.fuse([('bm25', lexical), ('dense', semantic)])
            hybrid_ms = lexical_ms + dense_ms + (time.perf_counter() - t) * 1000
            candidates.update({'dense': (semantic, dense_ms), 'hybrid': (hybrid, hybrid_ms)})
        outputs = {}
        for mode, (rows, milliseconds) in candidates.items():
            t = time.perf_counter()
            presented = [engine.present(r, text) for r in engine.select(rows, 50)]
            outputs[mode] = (presented, milliseconds + (time.perf_counter() - t) * 1000)
        for mode, (rows, milliseconds) in outputs.items():
            mode_results[mode] = {**score(rows[:50], query), 'query_ms': round(milliseconds, 2),
                                 'preview_chars5': sum(len(r['preview']) for r in rows[:5]),
                                 'results': rows[:5] if args.include_previews else
                                    [{k:v for k,v in row.items() if k != 'preview'} for row in rows[:5]]}
        exact = []
        if query['category'] in {'exact_symbol', 'exact-symbol', 'symbol'}:
            exact = engine.search(con, text, mode='symbol')
        results.append({'id': query['id'], 'query': text, 'split': query['split'],
                        'category': query['category'], 'modes': mode_results,
                        'exact_lookup': score(exact, query) if exact else None})
        print(f"{query['id']}: " + ', '.join(f'{m} rank={v["rank"]}' for m, v in mode_results.items()), flush=True)
    # Refuse to publish measurements if labels or indexed source changed mid-run.
    verify_labels(data, root)
    after_manifest = {name: digest for name, _, digest in engine.discover(root)}
    if after_manifest != source_manifest or file_hash(args.dataset) != frozen_hash:
        raise ValueError('Source/dataset changed during evaluation; discard run and rerun on a stable checkout.')
    report = {'run_at_utc': datetime.now(timezone.utc).isoformat(),
              'dataset_sha256': frozen_hash, 'record_fingerprint': record_fingerprint,
              'source_manifest_sha256': engine.sha(json.dumps(source_manifest, sort_keys=True)),
              'implementation_sha256': {name: file_hash(engine.LAB / name) for name in
                ['search_engine.py', 'semantic.py', 'language_parsers.py', 'paths.py', 'extractor.cjs', 'evaluate.py', 'package-lock.json']},
              'source_git_head': data['source_snapshot'].get('git_head'), 'index': stats,
              'refresh_ms': round(refresh_ms, 2),
              'embedding': dense.stats if dense else None,
              'protocol': {'candidate_limit': engine.CANDIDATES, 'rrf_k': engine.RRF_K,
                           'query_latency': 'warm retrieval plus up to 50 previews for scoring; excludes scan/startup; hybrid sums both retrieval costs',
                           'preview_measure': 'characters, not agent billing tokens',
                           'comparison': 'all retrieval modes use identical records',
                           'includes_source_previews': args.include_previews},
              'summary': aggregate(results), 'queries': results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report['summary'], indent=2))
    con.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, help='Override the dataset-relative source directory')
    parser.add_argument('--db', type=Path)
    parser.add_argument('--dataset', type=Path, default=engine.LAB / 'evaluation' / 'queries.json')
    parser.add_argument('--output', type=Path, help='Report destination; defaults to the private repository cache')
    parser.add_argument('--lexical-only', action='store_true')
    parser.add_argument('--include-previews', action='store_true', help='Include source snippets in the report')
    args = parser.parse_args()
    if not args.lexical_only:
        import os, sys
        python = venv_python()
        if python.exists() and Path(sys.prefix).resolve() != (APP / '.venv').resolve():
            os.execv(str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])
    run(args)
