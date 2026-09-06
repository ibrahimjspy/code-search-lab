"""Local code embeddings and exact cosine search; no remote inference.

The cache is addressed by model configuration and content, never SQLite row IDs.
That is essential because a deleted source row's numeric ID can be reused.
"""
import hashlib
import contextlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import numpy as np
from search_engine import eligible
from paths import cache_home, model_path, repo_cache

MODEL_ID = 'nomic-ai/CodeRankEmbed'
REVISION = '3c4b60807d71f79b43f3c4363786d9493691f8b1'
QUERY_PREFIX = 'Represent this query for searching relevant code: '
MAX_TOKENS = 2048


class CodeEncoder:
    def __init__(self):
        from sentence_transformers import SentenceTransformer
        import torch
        local_model = model_path()
        if not (local_model / 'model.safetensors').exists():
            raise RuntimeError('Pinned model weights missing; run python setup.py --embeddings.')
        started = time.perf_counter()
        self.device = ('cuda' if torch.cuda.is_available() else
                       'mps' if torch.backends.mps.is_available() else 'cpu')
        with contextlib.redirect_stdout(sys.stderr):
            self.model = SentenceTransformer(str(local_model), trust_remote_code=True,
                                            local_files_only=True, device=self.device)
        self.model.max_seq_length = MAX_TOKENS
        if self.device != 'cpu':
            self.model.half()
        self.signature = json.dumps({'model': MODEL_ID, 'revision': REVISION, 'max_tokens': MAX_TOKENS,
                                     'document_prefix': 'path + qualified symbol + code v1',
                                     'query_prefix': QUERY_PREFIX,
                                     'inference_dtype': 'float16' if self.device != 'cpu' else 'float32'}, sort_keys=True)
        self.load_ms = round((time.perf_counter() - started) * 1000)

    def _encode(self, texts):
        # Never silently compare full lexical sections to truncated neural ones.
        lengths = self.model.tokenizer(texts, truncation=False, padding=False)['input_ids']
        maximum = max(map(len, lengths), default=0)
        if maximum > MAX_TOKENS:
            raise ValueError(f'Embedding input exceeds {MAX_TOKENS} tokens ({maximum}); reduce source chunk size.')
        vectors = self.model.encode(texts, batch_size=16, normalize_embeddings=True,
                                    convert_to_numpy=True, show_progress_bar=False)
        vectors = np.asarray(vectors, dtype=np.float32)
        if not np.isfinite(vectors).all():
            raise RuntimeError('Embedding model returned non-finite vectors.')
        return vectors

    def documents(self, texts):
        return self._encode(texts)

    def query(self, text):
        return self._encode([QUERY_PREFIX + text])[0]


class DenseIndex:
    def __init__(self, con, path_filter=None, include_docs=False, encoder=None, cache_path=None):
        started = time.perf_counter()
        self.rows = eligible(con, path_filter, include_docs)
        self.encoder = encoder if encoder is not None else CodeEncoder()
        config = hashlib.sha256(self.encoder.signature.encode()).hexdigest()
        if cache_path is None:
            bound = con.execute("SELECT value FROM meta WHERE key='root'").fetchone()
            if bound is None:
                raise ValueError('Index has no source identity; refresh it before building embeddings.')
            cache_path = repo_cache(bound[0]) / 'vectors.sqlite'
        cache_path = Path(cache_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache = sqlite3.connect(cache_path, timeout=60)
        cache.execute('''CREATE TABLE IF NOT EXISTS vectors
          (config TEXT, content TEXT, dim INTEGER, data BLOB, PRIMARY KEY(config,content))''')
        stored = {key: np.frombuffer(data, dtype=np.float32).copy()
                  for key, dim, data in cache.execute('SELECT content,dim,data FROM vectors WHERE config=?', (config,))
                  if len(data) == dim * 4}
        missing = list({r['content_hash']: r for r in self.rows if r['content_hash'] not in stored}.values())
        missing.sort(key=lambda r: len(r['raw']))  # Minimize padding across batches.
        embed_started = time.perf_counter()
        try:
            for offset in range(0, len(missing), 128):
                batch = missing[offset:offset + 128]
                texts = [r['file'] + '\n' + r['qualified_symbol'] + '\n' + r['raw'] for r in batch]
                vectors = self.encoder.documents(texts)
                if len(vectors) != len(batch) or not np.isfinite(vectors).all():
                    raise RuntimeError('Invalid embedding batch.')
                with cache:
                    for row, vector in zip(batch, vectors):
                        vector = np.asarray(vector, dtype=np.float32)
                        norm = np.linalg.norm(vector)
                        if not norm > 0:
                            raise RuntimeError('Zero embedding vector.')
                        vector = vector / norm
                        stored[row['content_hash']] = vector
                        cache.execute('INSERT OR REPLACE INTO vectors VALUES (?,?,?,?)',
                                      (config, row['content_hash'], len(vector), vector.tobytes()))
                print(f'Embedded {min(offset + 128, len(missing))}/{len(missing)} new sections '
                      f'({time.perf_counter() - embed_started:.1f}s)', file=sys.stderr, flush=True)
        finally:
            cache.close()
        self.matrix = np.stack([stored[r['content_hash']] for r in self.rows]) if self.rows else np.empty((0, 0), dtype=np.float32)
        self.stats = {'model': MODEL_ID, 'revision': REVISION, 'rows': len(self.rows), 'new_embeddings': len(missing),
                      'vector_bytes': self.matrix.nbytes, 'device': getattr(self.encoder, 'device', 'test'),
                      'model_load_ms': getattr(self.encoder, 'load_ms', 0),
                      'embedding_ms': round((time.perf_counter() - embed_started) * 1000),
                      'ready_ms': round((time.perf_counter() - started) * 1000), 'max_tokens': MAX_TOKENS}

    def candidates(self, query, limit=50, vector=None):
        if not self.rows:
            return []
        vector = self.encoder.query(query) if vector is None else vector
        vector = np.asarray(vector, dtype=np.float32)
        norm = np.linalg.norm(vector)
        if not np.isfinite(vector).all() or not norm > 0:
            raise RuntimeError('Invalid query embedding.')
        scores = self.matrix @ (vector / norm)
        order = np.argsort(-scores, kind='stable')[:limit]
        return [{**self.rows[i], 'score': float(scores[i]), 'matched_by': 'dense'} for i in order]
