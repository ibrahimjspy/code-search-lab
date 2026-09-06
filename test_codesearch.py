"""Behavior tests against original, isolated source fixtures."""
import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

import search_engine as app


from language_parsers import javascript_available


@unittest.skipUnless(javascript_available(), 'Optional TypeScript parser not installed')
class SearchBehavior(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.con = app.connect(Path(self.temp.name) / "index.sqlite")
        self.addCleanup(self.con.close)

    def write(self, name, text):
        file = self.root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)

    def test_finds_behavior_and_returns_real_source_lines(self):
        self.write("queue/worker.ts", "class Provider {\n  async submitJob() {\n    return scheduler.submit();\n  }\n}\n")
        self.write("other.ts", "export function unrelated() {}")
        app.refresh(self.con, self.root)
        result = app.search(self.con, "submit job queue", 5)[0]
        self.assertEqual(result["file"], "queue/worker.ts")
        self.assertEqual(result["symbol"], "submitJob")
        self.assertEqual(result["start"], 2)
        self.assertIn("scheduler.submit", result["preview"])

    def test_changed_deleted_and_renamed_files_replace_old_results(self):
        self.write("old.ts", "function obsoleteMarker() {}")
        app.refresh(self.con, self.root)
        self.assertTrue(app.search(self.con, "obsoleteMarker", 5))
        self.write("old.ts", "\n\nfunction replacementMarker() {}")
        changed = app.refresh(self.con, self.root)
        self.assertEqual(changed["updated"], 1)
        self.assertFalse(app.search(self.con, "obsolete", 5))
        self.assertEqual(app.search(self.con, "replacementMarker", 5)[0]["start"], 3)
        (self.root / "old.ts").rename(self.root / "new.ts")
        renamed = app.refresh(self.con, self.root)
        self.assertEqual(renamed["deleted"], 1)
        self.assertEqual(app.search(self.con, "replacementMarker", 5)[0]["file"], "new.ts")
        (self.root / "new.ts").unlink()
        app.refresh(self.con, self.root)
        self.assertFalse(app.search(self.con, "replacementMarker", 5))

    def test_ignores_noise_and_reuses_unchanged_records(self):
        self.write(".gitignore", "ignored.ts\n")
        for name in ["ignored.ts", "node_modules/noise.ts", ".env.ts"]:
            self.write(name, "function secretMarker() {}")
        self.write("main.ts", "function visibleMarker() {}")
        stats = app.refresh(self.con, self.root)
        self.assertEqual(stats["files"], 1)
        self.assertFalse(app.search(self.con, "secret", 5))
        self.assertEqual(app.refresh(self.con, self.root)["updated"], 0)

    def test_separates_checkouts_and_handles_query_punctuation(self):
        self.write("main.ts", "function submitJob() {}")
        app.refresh(self.con, self.root)
        self.assertTrue(app.search(self.con, 'submit job "); DROP TABLE chunks; --', 5))
        self.assertEqual(app.search(self.con, "where is the", 5), [])
        with self.assertRaises(ValueError):
            app.refresh(self.con, self.root.parent)

    def test_keeps_method_locals_and_filters_docs_and_paths(self):
        self.write("src/provider.ts", "class Provider {\n  async deliver() {\n    const token = 1;\n    return dispatch(token);\n  }\n}\n")
        self.write("docs.md", "# dispatch\nDispatch the token")
        app.refresh(self.con, self.root)
        results = app.search(self.con, "dispatch", 5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["symbol"], "deliver")
        self.assertEqual(len(app.search(self.con, "dispatch", 5, include_docs=True)), 2)
        self.assertEqual(app.search(self.con, "dispatch", 5, path_filter="missing/"), [])

    def test_exact_symbols_keep_stopwords_and_class_identity(self):
        self.write('src.ts', 'class A { get() {} }\nclass B { get() {} }')
        app.refresh(self.con, self.root)
        self.assertEqual(len(app.search(self.con, 'get', mode='symbol')), 2)
        result = app.search(self.con, 'B.get()', mode='symbol')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['qualified_symbol'], 'B.get')

    def test_exact_lookup_prefers_overload_implementation(self):
        self.write('src.ts', 'class A {\n run(x: string): string;\n run(x: any) { return x; }\n}')
        app.refresh(self.con, self.root)
        result = app.search(self.con, 'A.run', mode='symbol')[0]
        self.assertEqual(result['start'], 3)
        self.assertIn('return x', result['preview'])

    def test_invalid_syntax_preserves_previous_index(self):
        self.write('src.ts', 'function intact() {}')
        app.refresh(self.con, self.root)
        self.write('src.ts', 'function broken( {')
        with self.assertRaises(RuntimeError):
            app.refresh(self.con, self.root)
        self.assertEqual(app.search(self.con, 'intact', mode='symbol')[0]['symbol'], 'intact')

    def test_deleted_record_reuse_cannot_reuse_stale_content_identity(self):
        self.write('a.ts', 'function first() {}')
        app.refresh(self.con, self.root)
        before = app.search(self.con, 'first', mode='symbol')[0]
        self.write('a.ts', 'function second() {}')
        app.refresh(self.con, self.root)
        after = app.search(self.con, 'second', mode='symbol')[0]
        self.assertNotEqual(before['content_hash'], after['content_hash'])

    def test_rrf_rewards_agreement_without_mutating_scores(self):
        lexical = [{'id': 1, 'score': 100}, {'id': 2, 'score': 50}]
        dense = [{'id': 2, 'score': .9}, {'id': 3, 'score': .8}]
        result = app.fuse([('bm25', lexical), ('dense', dense)])
        self.assertEqual(result[0]['id'], 2)
        self.assertEqual(result[0]['ranks'], {'bm25': 2, 'dense': 1})
        self.assertEqual(lexical[1]['score'], 50)

    def test_bounded_chunks_keep_long_line_locations(self):
        record = {'start': 3, 'text': 'x' * 4000 + '\nnext\n', 'symbol': 'f'}
        chunks = list(app.bounded(record))
        self.assertEqual([c['start'] for c in chunks], [3, 3, 3])
        self.assertEqual(chunks[-1]['end'], 4)
        self.assertTrue(all(len(c['raw']) <= 1800 for c in chunks))


if __name__ == "__main__":
    unittest.main()
