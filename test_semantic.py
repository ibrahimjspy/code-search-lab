"""Cache correctness and ranking tests; model quality is evaluated on real source."""
from pathlib import Path
import tempfile
import unittest
import numpy as np
import search_engine as engine
from semantic import DenseIndex


class FakeEncoder:
    signature = 'fixture-v1'
    calls = 0
    def documents(self, texts):
        self.calls += len(texts)
        return np.array([[1, 0] if 'alpha' in text else [0, 1] for text in texts], dtype=np.float32)
    def query(self, text):
        return np.array([1, 0], dtype=np.float32)


class DenseBehavior(unittest.TestCase):
    def test_cache_keys_use_content_and_model_not_recycled_row_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = engine.connect(Path(tmp) / 'source.sqlite')
            cache = Path(tmp) / 'vectors.sqlite'
            def insert(text):
                con.execute('DELETE FROM records')
                con.execute('''INSERT INTO records(id,file,symbol,qualified_symbol,kind,start,end,raw,content_hash)
                  VALUES (1,'a.ts','f','A.f','method',1,1,?,?)''', (text, engine.sha(text)))
                con.commit()
            insert('alpha')
            encoder = FakeEncoder()
            first = DenseIndex(con, encoder=encoder, cache_path=cache)
            self.assertEqual(first.stats['new_embeddings'], 1)
            self.assertAlmostEqual(first.candidates('alpha')[0]['score'], 1)
            self.assertEqual(DenseIndex(con, encoder=encoder, cache_path=cache).stats['new_embeddings'], 0)
            insert('beta')
            changed = DenseIndex(con, encoder=encoder, cache_path=cache)
            self.assertEqual(changed.stats['new_embeddings'], 1)
            self.assertAlmostEqual(changed.candidates('alpha')[0]['score'], 0)
            encoder.signature = 'fixture-v2'
            self.assertEqual(DenseIndex(con, encoder=encoder, cache_path=cache).stats['new_embeddings'], 1)
            con.execute('DELETE FROM records')
            con.commit()
            self.assertEqual(DenseIndex(con, encoder=encoder, cache_path=cache).candidates('alpha'), [])
            con.close()

    def test_metrics_do_not_confuse_neighbor_with_actual_implementation(self):
        from evaluate import score
        query = {'expected': [{'file': 'a.ts', 'symbol': 'send', 'start': 10, 'end': 40,
                               'evidence_start': 20, 'evidence_end': 21}]}
        rows = [{'file': 'a.ts', 'symbol': 'configure', 'start': 1, 'end': 9,
                 'preview_start': 1, 'preview': 'wrong'},
                {'file': 'a.ts', 'symbol': 'send', 'start': 10, 'end': 30,
                 'preview_start': 20, 'preview': 'send\nreturn'}]
        result = score(rows, query)
        self.assertEqual(result['rank'], 2)
        self.assertEqual(result['evidence_preview_coverage5'], 1)


if __name__ == '__main__':
    unittest.main()
