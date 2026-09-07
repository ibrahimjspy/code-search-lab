"""One authenticated loopback service per source root, shared by CLI and MCP."""
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from paths import APP, source_root, repo_cache, venv_python
from intelligence import Intelligence
import search_engine as engine

_children = {}


class FileUpdates:
    def __init__(self, intelligence, reconcile_seconds=30):
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
        self.kb = intelligence
        self.condition = threading.Condition()
        self.dirty = set()
        self.full = False
        self.received = self.processed = 0
        self.stopping = False
        self.interval = reconcile_seconds
        parent = self
        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                if event.event_type not in {'modified','created','deleted','moved'}: return
                if event.is_directory and event.event_type=='modified': return
                for value in [event.src_path,getattr(event,'dest_path',None)]:
                    if value: parent.record(value)
        self.observer = Observer()
        self.observer.schedule(Handler(),str(self.kb.root),recursive=True)
        # A worktree can keep Git metadata outside its source root.
        try:
            result = subprocess.run(['git','-C',str(self.kb.root),'rev-parse','--absolute-git-dir'],
                                    capture_output=True,text=True,encoding='utf-8',timeout=5)
            self.git_dir = Path(result.stdout.strip()).resolve() if result.returncode==0 else None
        except (FileNotFoundError,subprocess.TimeoutExpired): self.git_dir = None
        if self.git_dir and not self.git_dir.is_relative_to(self.kb.root):
            self.observer.schedule(Handler(),str(self.git_dir),recursive=True)
        self.observer.start()
        self.thread = threading.Thread(target=self._worker,daemon=True)
        self.thread.start()

    def record(self, path):
        full_path = Path(os.path.abspath(path))
        git_metadata = self.git_dir and full_path.is_relative_to(self.git_dir)
        if git_metadata:
            relative = full_path.relative_to(self.git_dir)
            if relative.name not in {'HEAD','index','packed-refs','exclude','config'} and 'refs' not in relative.parts:
                return
            name = None
        else:
            if not full_path.is_relative_to(self.kb.root): return
            name = full_path.relative_to(self.kb.root).as_posix()
            if name=='.': return
            if set(Path(name).parts) & engine.SKIP_PARTS: return
            if full_path.resolve().is_relative_to(engine.cache_home().resolve()): return
            if name in {'.gitignore','.codesearchignore','tsconfig.json'} or name.endswith('/tsconfig.json'):
                name = None
        with self.condition:
            self.received += 1
            if name is None: self.full = True
            else: self.dirty.add(name)
            self.condition.notify_all()

    def _worker(self):
        last_full = time.monotonic()
        while True:
            with self.condition:
                self.condition.wait(.05)
                if self.stopping: return
                reconcile = time.monotonic()-last_full >= self.interval
                if not self.dirty and not self.full and not reconcile: continue
                paths = None if self.full or reconcile else sorted(self.dirty)
                sequence = self.received
                self.dirty.clear()
                self.full = False
            try: self.kb.update(paths)
            except Exception: pass  # Intelligence retains the error; queries fail visibly.
            if paths is None: last_full = time.monotonic()
            with self.condition:
                self.processed = max(self.processed,sequence)
                self.condition.notify_all()

    def flush(self, timeout=30):
        deadline = time.monotonic()+timeout
        with self.condition:
            target = self.received
            while self.processed<target:
                remaining = deadline-time.monotonic()
                if remaining<=0: raise TimeoutError('Index update is still pending.')
                self.condition.wait(min(remaining,.1))
            return {'observed_events':target,'processed_events':self.processed,
                    'consistency':'observed-events; use sync_paths or full_sync for stricter freshness'}

    def close(self):
        self.observer.stop()
        self.observer.join(timeout=5)
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        self.thread.join(timeout=35)


def descriptor(root):
    return repo_cache(root)/'service.json'


def request(root, operation, parameters=None, timeout=120):
    info = json.loads(descriptor(root).read_text(encoding='utf-8'))
    if info.get('root')!=str(Path(root).expanduser().resolve()) or not isinstance(info.get('port'),int):
        raise RuntimeError('Invalid service descriptor.')
    url = f"http://127.0.0.1:{info['port']}/rpc"
    data = json.dumps({'operation':operation,'parameters':parameters or {}}).encode()
    call = urllib.request.Request(url,data=data,headers={'Authorization':'Bearer '+info['token'],
                                  'Content-Type':'application/json'})
    # Local traffic never goes through an environment-configured HTTP proxy.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs): return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    try:
        with opener.open(call,timeout=timeout) as response: payload = json.load(response)
    except urllib.error.HTTPError as exc:
        try: payload = json.load(exc)
        except Exception: raise RuntimeError('Service rejected the request.') from exc
    if 'error' in payload: raise RuntimeError(payload['error'])
    return payload['result']


def running(root):
    try: return request(root,'status',timeout=1)
    except (OSError,ValueError,KeyError,RuntimeError): return None


def start(root, warm=False):
    root = source_root(root)
    status = running(root)
    if status is None:
        cache = repo_cache(root)
        cache.mkdir(parents=True,exist_ok=True,mode=0o700)
        python = venv_python() if venv_python().exists() else Path(sys.executable)
        log = open(cache/'service.log','ab',buffering=0)
        kwargs = {'start_new_session':True} if os.name!='nt' else {
            'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
        child = subprocess.Popen([str(python),str(APP/'service.py'),'--repo',str(root)],
                                 stdin=subprocess.DEVNULL,stdout=log,stderr=log,cwd=APP,**kwargs)
        _children[str(root)] = child
        threading.Thread(target=child.wait,daemon=True).start()
        log.close()
        deadline = time.monotonic()+120
        while time.monotonic()<deadline:
            status = running(root)
            if status is not None: break
            if child.poll() is not None:
                raise RuntimeError('Service could not start. Run python setup.py --service; inspect the private service log.')
            time.sleep(.1)
        else: raise TimeoutError('Service startup is still pending; inspect its private log.')
    if warm: request(root,'warm',timeout=1800)
    return request(root,'status')


def _wait_process(pid, timeout):
    """Wait without signaling a PID, including a daemon started by another client."""
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE,wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000,False,pid)  # SYNCHRONIZE only.
        if not handle:
            if ctypes.get_last_error() in {87,1168}: return  # Already exited.
            raise RuntimeError('Cannot confirm service process exit.')
        try:
            if kernel.WaitForSingleObject(handle,int(timeout*1000))!=0:
                raise TimeoutError('Service is still stopping.')
        finally: kernel.CloseHandle(handle)
    else:
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            try: os.kill(pid,0)  # Existence probe, not a termination signal.
            except ProcessLookupError: return
            time.sleep(.05)
        raise TimeoutError('Service is still stopping.')


def stop(root, timeout=30):
    root = Path(root).expanduser().resolve()
    try: info = json.loads(descriptor(root).read_text(encoding='utf-8'))
    except (FileNotFoundError,ValueError): info = None
    if running(root): request(root,'stop')
    child = _children.get(str(root))
    if child is not None:
        child.wait(timeout=timeout)
        _children.pop(str(root),None)
    elif info is not None:
        _wait_process(info['pid'],timeout)
    # Remove only this stopped instance's descriptor, never a replacement server.
    if info and descriptor(root).exists():
        current = json.loads(descriptor(root).read_text(encoding='utf-8'))
        if current.get('token')==info.get('token'): descriptor(root).unlink(missing_ok=True)
    return {'stopped':True}


def serve(root):
    from filelock import FileLock, Timeout
    root = source_root(root)
    cache = repo_cache(root)
    cache.mkdir(parents=True,exist_ok=True,mode=0o700)
    lock = FileLock(str(cache/'service.lock'))
    try: lock.acquire(timeout=0)
    except Timeout: return
    kb = updates = server = None
    token = secrets.token_urlsafe(32)
    owned = False
    try:
        kb = Intelligence(root)
        updates = FileUpdates(kb)
        # Reconcile changes that happened between initial indexing and watch startup.
        kb.update()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                if (self.path!='/rpc' or self.headers.get('Origin') is not None
                    or not secrets.compare_digest(self.headers.get('Authorization','').encode(),('Bearer '+token).encode())):
                    self.send_error(403); return
                try:
                    size = int(self.headers.get('Content-Length','0'))
                    if not 0<size<=1_048_576: raise ValueError('Request must be at most 1 MiB.')
                    self.connection.settimeout(10)
                    incoming = json.loads(self.rfile.read(size))
                    operation, parameters = incoming.get('operation'),incoming.get('parameters',{})
                    if not isinstance(parameters,dict): raise ValueError('Parameters must be an object.')
                    if operation=='status': result = kb.status()
                    elif operation=='stop':
                        result = {'stopping':True}
                        threading.Thread(target=server.shutdown,daemon=True).start()
                    else:
                        freshness = updates.flush()
                        full_sync = parameters.pop('full_sync',False)
                        sync_paths = parameters.pop('sync_paths',[])
                        if full_sync: kb.update()
                        elif sync_paths:
                            if not isinstance(sync_paths,list) or len(sync_paths)>100: raise ValueError('sync_paths must contain at most 100 paths.')
                            from editor_context import relative_path
                            kb.update([relative_path(root,p) for p in sync_paths])
                        if operation=='query': result = kb.query(**parameters)
                        elif operation=='read':
                            with kb.lock: result = kb.read(**parameters)
                        elif operation=='warm':
                            with kb.lock: result = kb.dense_index().stats
                        elif operation=='sync': result = kb.update()
                        else: raise ValueError('Unknown service operation.')
                        result['freshness'] = freshness | {'full_sync':bool(full_sync or operation=='sync'),
                                                         'synced_paths':sync_paths}
                    body = json.dumps({'result':result}).encode()
                    status = 200
                except Exception as exc:
                    body = json.dumps({'error':str(exc)}).encode()
                    status = 400
                self.send_response(status)
                self.send_header('Content-Type','application/json; charset=utf-8')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                try: self.wfile.write(body)
                except (BrokenPipeError,ConnectionResetError): pass
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        server.daemon_threads = True
        info = {'root':str(root),'port':server.server_port,'pid':os.getpid(),'token':token,'protocol':1}
        temp = cache/'service.json.tmp'
        fd = os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as file: json.dump(info,file)
        temp.replace(descriptor(root)); owned = True
        def shutdown(*unused): threading.Thread(target=server.shutdown,daemon=True).start()
        signal.signal(signal.SIGTERM,shutdown)
        signal.signal(signal.SIGINT,shutdown)
        server.serve_forever(poll_interval=.1)
    finally:
        if updates: updates.close()
        if server: server.server_close()
        if kb: kb.close()
        if owned: descriptor(root).unlink(missing_ok=True)
        lock.release()


if __name__=='__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo',required=True)
    serve(parser.parse_args().repo)
