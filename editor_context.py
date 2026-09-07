"""Bounded, request-local editor context. No editor/provider dependency."""
from pathlib import Path
import subprocess


def relative_path(root, value):
    path = Path(value)
    full = path.resolve() if path.is_absolute() else (root/path).resolve()
    if not full.is_relative_to(root): raise ValueError('Context path escapes the source directory.')
    return full.relative_to(root).as_posix()


def collect(root, con, context=None, include_diff=False):
    context = context or {}
    if not isinstance(context,dict): raise ValueError('Editor context must be an object.')
    active = relative_path(root,context['active_file']) if context.get('active_file') else None
    selection = context.get('selection')
    output = {'active_file':active,'selection':None,'diagnostics':[], 'git':None}
    if selection is not None:
        if not isinstance(selection,dict): raise ValueError('Selection must be an object.')
        if 'text' in selection:
            text = selection['text']
            if not isinstance(text,str) or len(text)>16000: raise ValueError('Selection text must be at most 16000 characters.')
            output['selection'] = {'text':text,'source':'client','indexed_buffer':False}
        elif active:
            row = con.execute('SELECT text FROM files WHERE path=?',(active,)).fetchone()
            if row is None: raise ValueError('Selected file is not indexed.')
            start,end = selection.get('start_line',1),selection.get('end_line',selection.get('start_line',1))
            if not isinstance(start,int) or not isinstance(end,int) or not 1<=start<=end<=start+199:
                raise ValueError('Selection must contain at most 200 lines (one-based).')
            output['selection'] = {'start_line':start,'end_line':end,
                'text':'\n'.join(row['text'].splitlines()[start-1:end])[:16000],'source':'indexed_snapshot'}
    diagnostics = context.get('diagnostics',[])
    if not isinstance(diagnostics,list) or len(diagnostics)>50: raise ValueError('At most 50 diagnostics are accepted.')
    for diagnostic in diagnostics:
        if not isinstance(diagnostic,dict) or not isinstance(diagnostic.get('message'),str):
            raise ValueError('Diagnostics require a message.')
        output['diagnostics'].append({'file':relative_path(root,diagnostic.get('file') or active or '.'),
            'line':diagnostic.get('line'), 'severity':str(diagnostic.get('severity','unknown'))[:30],
            'message':diagnostic['message'][:2000]})
    if include_diff or context.get('include_git_diff'):
        def git(args):
            try:
                result = subprocess.run(['git','-C',str(root),*args],capture_output=True,timeout=5)
                return result.stdout.decode('utf-8',errors='replace') if result.returncode==0 else ''
            except (FileNotFoundError,subprocess.TimeoutExpired): return ''
        head = git(['rev-parse','--verify','HEAD']).strip()
        revision = ['HEAD'] if head else ['--cached']
        names = git(['diff','--no-ext-diff','--no-textconv','--name-only',*revision,'--']).splitlines()[:20]
        # Only indexed files can contribute source text to automatic context.
        known = {r[0] for r in con.execute('SELECT path FROM files')}
        names = [n for n in names if n in known]
        diff = git(['diff','--no-ext-diff','--no-textconv','--unified=3',*revision,'--',*names]) if names else ''
        output['git'] = {'changed_files':names,'diff':diff[:16000],'truncated':len(diff)>16000,
                         'scope':'tracked changes against HEAD' if head else 'staged changes without HEAD'}
    return output
