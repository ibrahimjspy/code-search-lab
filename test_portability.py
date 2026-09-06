"""Generic source selection, language fallback and platform-path tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import paths
import search_engine as engine
from language_parsers import python_records


class PortableBehavior(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'plain source'
        self.root.mkdir()
        self.override = patch.dict(os.environ, {'CODESEARCH_CACHE_DIR': str(self.base / 'cache')})
        self.override.start()
        self.addCleanup(self.override.stop)

    def test_current_directory_default_and_distinct_repo_caches(self):
        other = self.base / 'other' / 'plain source'
        other.mkdir(parents=True)
        with patch('paths.Path.cwd', return_value=self.root):
            self.assertEqual(paths.source_root(), self.root.resolve())
        self.assertNotEqual(paths.default_db(self.root), paths.default_db(other))
        self.assertEqual(paths.default_db(self.root), paths.default_db(self.root / '.'))

    def test_non_git_multilanguage_text_and_binary_filtering(self):
        (self.root / 'worker.go').write_text('func dispatchQueue() { enqueue(task) }')
        (self.root / 'other.rs').write_text('fn clean_cache() { remove_expired(); }')
        (self.root / 'picture.dat').write_bytes(b'\x00\xff\x00')
        (self.root / 'odd.dat').write_bytes(b'\xff\xfe')
        (self.root / '.codesearchignore').write_text('local/\n*.min.js\n')
        (self.root / 'local').mkdir()
        (self.root / 'local' / 'note.py').write_text('private_note = 1')
        (self.root / 'packed.min.js').write_text('minified();')
        with patch('search_engine.shutil.which', return_value=None):
            found = list(engine.discover(self.root))
        self.assertEqual({name for name, _, _ in found}, {'worker.go', 'other.rs'})
        con = engine.connect(self.base / 'test.sqlite')
        self.addCleanup(con.close)
        engine.refresh(con, self.root)
        result = engine.search(con, 'dispatch queue')[0]
        self.assertEqual(result['file'], 'worker.go')
        self.assertEqual(result['kind'], 'window')

    def test_python_ast_works_without_node(self):
        text = '# cache example\nclass Cache:\n    # fetch a value\n    @property\n    def value(self):\n        local = 1\n        return local\n'
        (self.root / 'cache.py').write_text(text)
        with patch('language_parsers.javascript_available', return_value=False):
            con = engine.connect(self.base / 'python.sqlite')
            self.addCleanup(con.close)
            engine.refresh(con, self.root)
        result = engine.search(con, 'Cache.value', mode='symbol')[0]
        self.assertEqual(result['start'], 3)
        self.assertEqual(result['end'], 7)
        self.assertIn('@property', result['preview'])
        self.assertFalse(engine.exact_candidates(con, 'local'))

    def test_missing_js_adapter_still_indexes_text(self):
        (self.root / 'queue.ts').write_text('export function enqueue() { return 1; }')
        con = engine.connect(self.base / 'fallback.sqlite')
        self.addCleanup(con.close)
        with patch('language_parsers.javascript_available', return_value=False):
            engine.refresh(con, self.root)
        self.assertEqual(engine.search(con, 'enqueue')[0]['kind'], 'window')
        self.assertFalse(engine.exact_candidates(con, 'enqueue'))

    def test_cli_from_unrelated_directory_and_cache_isolation(self):
        for root, name in [(self.root, 'first'), (self.base / 'second', 'second')]:
            root.mkdir(exist_ok=True)
            (root / 'source.py').write_text(f'def {name}():\n    return 1\n')
            call = subprocess.run([sys.executable, str(paths.APP / 'codesearch'), 'symbol', name, '--json'],
                                  cwd=root, capture_output=True, text=True, check=True)
            output = json.loads(call.stdout)
            self.assertEqual(output['repo'], str(root.resolve()))
            self.assertEqual(output['results'][0]['symbol'], name)
            self.assertTrue(paths.default_db(root).is_file())
            self.assertFalse((root / '.index').exists())

    def test_platform_cache_and_interpreter_selection(self):
        with patch.dict(os.environ, {}, clear=True), patch('paths.Path.home', return_value=self.base / 'home'):
            with patch('paths.sys.platform', 'win32'), patch.dict(os.environ, {'LOCALAPPDATA': str(self.base / 'local')}):
                self.assertEqual(paths.cache_home(), self.base / 'local' / 'code-search-lab')
                self.assertEqual(paths.venv_python().parts[-2:], ('Scripts', 'python.exe'))
            with patch('paths.sys.platform', 'linux'), patch.dict(os.environ, {'XDG_CACHE_HOME': str(self.base / 'xdg')}):
                self.assertEqual(paths.cache_home(), self.base / 'xdg' / 'code-search-lab')
                self.assertEqual(paths.venv_python().parts[-2:], ('bin', 'python'))
            with patch('paths.sys.platform', 'darwin'):
                self.assertEqual(paths.cache_home(), Path.home() / 'Library' / 'Caches' / 'code-search-lab')

    def test_python_parse_error_preserves_previous_records(self):
        file = self.root / 'functions.py'
        file.write_text('def keep():\n    return 1\n')
        con = engine.connect(self.base / 'parse.sqlite')
        self.addCleanup(con.close)
        engine.refresh(con, self.root)
        file.write_text('def broken(:')
        with self.assertRaises(RuntimeError):
            engine.refresh(con, self.root)
        self.assertTrue(engine.exact_candidates(con, 'keep'))

    def test_caches_inside_source_are_not_indexed(self):
        cache = self.root / 'custom-cache'
        cache.mkdir()
        (cache / 'cached.py').write_text('def cached(): pass')
        (self.root / 'real.py').write_text('def real(): pass')
        with patch.dict(os.environ, {'CODESEARCH_CACHE_DIR': str(cache)}):
            self.assertEqual([name for name, _, _ in engine.discover(self.root)], ['real.py'])

    def test_unicode_identifiers_are_searchable(self):
        (self.root / 'unicode.py').write_text('def café():\n    return "coffee"\n', encoding='utf-8')
        con = engine.connect(self.base / 'unicode.sqlite')
        self.addCleanup(con.close)
        engine.refresh(con, self.root)
        self.assertEqual(engine.search(con, 'café')[0]['symbol'], 'café')
        self.assertEqual(engine.search(con, 'café', mode='symbol')[0]['symbol'], 'café')

    def test_configured_cache_does_not_require_home_lookup(self):
        with patch.dict(os.environ, {}, clear=True), patch('paths.Path.home', side_effect=RuntimeError('no home')):
            with patch('paths.sys.platform', 'win32'), patch.dict(os.environ, {'LOCALAPPDATA': str(self.base)}):
                self.assertEqual(paths.cache_home(), self.base / 'code-search-lab')
            with patch('paths.sys.platform', 'linux'), patch.dict(os.environ, {'XDG_CACHE_HOME': str(self.base)}):
                self.assertEqual(paths.cache_home(), self.base / 'code-search-lab')

    def test_javascript_unicode_survives_parser_wire_encoding(self):
        from language_parsers import javascript_available
        if not javascript_available():
            self.skipTest('Optional TypeScript parser not installed')
        (self.root / 'unicode.ts').write_text('class Café { ajouter() { return "été"; } }', encoding='utf-8')
        con = engine.connect(self.base / 'js-unicode.sqlite')
        self.addCleanup(con.close)
        engine.refresh(con, self.root)
        result = engine.search(con, 'Café.ajouter', mode='symbol')[0]
        self.assertIn('été', result['preview'])


if __name__ == '__main__':
    unittest.main()
