"""Layer-one behavior checks against original synthetic source only."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from intelligence import Intelligence
from service import FileUpdates
from language_parsers import javascript_available


class IntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base/'source'; self.root.mkdir()
        self.env = patch.dict(os.environ,{'CODESEARCH_CACHE_DIR':str(self.base/'cache')})
        self.env.start(); self.addCleanup(self.env.stop)

    def write(self,file,text):
        path = self.root/file; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(text,encoding='utf-8')

    def kb(self,**kwargs):
        kb = Intelligence(self.root,**kwargs)
        self.addCleanup(kb.close)
        return kb

    def test_incremental_changes_do_not_drop_other_files(self):
        self.write('one.py','def first(): return 1')
        self.write('two.py','def second(): return 2')
        kb = self.kb()
        self.write('one.py','def changed(): return 3')
        kb.update(['one.py'])
        self.assertEqual(kb.stats['scanned'],1)
        self.assertEqual(kb.full_scans,1)
        self.assertTrue(kb.query('second')['results'])
        (self.root/'one.py').unlink(); kb.update(['one.py'])
        self.assertFalse(kb.query('changed',mode='symbol')['results'])
        self.assertEqual(kb.stats['files'],1)

    def test_native_watcher_updates_and_invalidates_graph(self):
        self.write('one.py','def first(): return 1')
        kb = self.kb()
        self.assertTrue(kb.query('first',mode='definitions')['results'])
        watcher = FileUpdates(kb,reconcile_seconds=3600)
        self.addCleanup(watcher.close)
        generation = kb.generation
        self.write('one.py','def replacement(): return 2')
        deadline = time.monotonic()+8
        while kb.generation==generation and time.monotonic()<deadline: time.sleep(.05)
        watcher.flush()
        self.assertGreater(kb.generation,generation)
        self.assertTrue(kb.query('replacement',mode='definitions')['results'])
        self.assertFalse(kb.query('first',mode='definitions')['results'])
        scans = kb.full_scans
        for _ in range(3): kb.query('replacement')
        self.assertEqual(kb.full_scans,scans)

    def test_watcher_renames_and_periodic_recovery(self):
        self.write('one.py','def first(): return 1')
        kb = self.kb()
        watcher = FileUpdates(kb,reconcile_seconds=.2)
        self.addCleanup(watcher.close)
        (self.root/'one.py').rename(self.root/'two.py')
        deadline = time.monotonic()+8
        while time.monotonic()<deadline:
            found = kb.query('first',mode='symbol')['results']
            if found and found[0]['file']=='two.py': break
            time.sleep(.05)
        self.assertEqual(found[0]['file'],'two.py')
        # Simulate missed native notifications; periodic reconciliation repairs it.
        watcher.observer.stop(); watcher.observer.join(timeout=5)
        self.write('two.py','def recovered(): return 2')
        deadline = time.monotonic()+8
        while time.monotonic()<deadline and not kb.query('recovered',mode='symbol')['results']: time.sleep(.05)
        self.assertTrue(kb.query('recovered',mode='symbol')['results'])

    def test_ignore_changes_reconcile_watcher_scope(self):
        self.write('one.py','def first(): return 1')
        kb = self.kb()
        watcher = FileUpdates(kb,reconcile_seconds=3600)
        self.addCleanup(watcher.close)
        self.write('.codesearchignore','one.py\n')
        deadline = time.monotonic()+8
        while kb.stats['files'] and time.monotonic()<deadline: time.sleep(.05)
        self.assertFalse(kb.query('first',mode='symbol')['results'])

    def test_router_regex_and_context_are_explicit(self):
        self.write('one.py','def calculate_total():\n    return 42\n')
        kb = self.kb()
        self.assertEqual(kb.query('calculate_total')['route']['mode'],'symbol')
        self.assertEqual(kb.query(r're: return\s+42')['route']['mode'],'regex')
        self.assertEqual(kb.query('sum of values')['route']['mode'],'bm25')
        answer = kb.query('',context={'active_file':'one.py','selection':{'text':'calculate_total'},
                          'diagnostics':[{'file':'one.py','line':2,'message':'example diagnostic'}]})
        self.assertEqual(answer['route']['mode'],'symbol')
        self.assertEqual(answer['editor_context']['selection']['source'],'client')
        with self.assertRaises(ValueError): kb.query('anything',context={'active_file':'../outside.py'})
        with self.assertRaises(ValueError): kb.read('../outside.py')

    def test_git_context_and_selection_are_bounded(self):
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        self.write('one.py','def value(): return 1\n')
        subprocess.run(['git','-C',str(self.root),'add','.'],check=True)
        kb = self.kb()
        answer = kb.query('value',context={'active_file':'one.py','selection':{'start_line':1,'end_line':1}},include_git_diff=True)
        self.assertIn('one.py',answer['editor_context']['git']['changed_files'])
        self.assertIn('return 1',answer['editor_context']['selection']['text'])

    def test_python_import_aliases_and_parameter_shadowing(self):
        self.write('helpers.py','def send(): return 1\n')
        self.write('app.py','from helpers import send as deliver\ndef run(): return deliver()\ndef shadow(deliver): return deliver()\n')
        kb = self.kb()
        refs = kb.query('send',mode='references')['results']
        self.assertTrue(any(r['file']=='app.py' and r['line']==2 for r in refs))
        self.assertFalse(any(r['file']=='app.py' and r['line']==3 and r['targets'] for r in refs))
        self.assertEqual(kb.query('run',mode='calls')['results'][0]['resolved_targets'][0]['symbol'],'send')
        self.assertEqual(kb.query('shadow',mode='calls')['results'][0]['confidence'],'external_or_dynamic')
        self.assertEqual(kb.query('app.py',mode='imports')['results'][0]['target_file'],'helpers.py')

    @unittest.skipUnless(javascript_available(),'Optional TS compiler not installed')
    def test_typescript_compiler_resolves_alias_and_callers(self):
        self.write('helpers.ts','export function send() { return 1; }')
        self.write('app.ts',"import {send as deliver} from './helpers';\nexport function run() { return deliver(); }")
        kb = self.kb()
        refs = kb.query('send',mode='references')['results']
        self.assertTrue(any(r['file']=='app.ts' and r['line']==2 for r in refs))
        calls = kb.query('run',mode='calls')['results']
        self.assertEqual(calls[0]['resolved_targets'][0]['symbol'],'send')
        self.assertEqual(calls[0]['confidence'],'compiler')
        self.assertTrue(kb.query('send',mode='callers')['results'])
        definition = kb.query('',mode='definitions',file='app.ts',line=2,column=32)['results']
        self.assertEqual(definition[0]['file'],'helpers.ts')

    def test_warm_model_is_reused_after_source_updates(self):
        from test_semantic import FakeEncoder
        factory_calls = []
        def factory(): factory_calls.append(1); return FakeEncoder()
        self.write('one.py','def alpha(): return 1')
        kb = self.kb(encoder_factory=factory)
        kb.query('alpha',mode='dense'); kb.query('alpha',mode='hybrid')
        self.write('one.py','def beta(): return 2'); kb.update(['one.py'])
        answer = kb.query('beta',mode='dense')
        self.assertEqual(answer['results'][0]['symbol'],'beta')
        self.assertEqual(len(factory_calls),1)
        self.assertEqual(kb.model_loads,1)


if __name__=='__main__': unittest.main()
