"""Repository-independent paths for source, private caches and optional tools."""
import hashlib
import os
from pathlib import Path
import sys

APP = Path(__file__).resolve().parent


def source_root(path=None):
    root = Path(path if path is not None else Path.cwd()).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('Source directory does not exist: ' + str(root))
    return root


def cache_home():
    override = os.environ.get('CODESEARCH_CACHE_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == 'win32':
        configured = os.environ.get('LOCALAPPDATA')
        base = Path(configured) if configured else Path.home() / 'AppData' / 'Local'
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Caches'
    else:
        configured = os.environ.get('XDG_CACHE_HOME')
        base = Path(configured) if configured else Path.home() / '.cache'
    return base / 'code-search-lab'


def repo_cache(root):
    identity = os.path.normcase(str(source_root(root)))
    key = hashlib.sha256(identity.encode()).hexdigest()[:24]
    return cache_home() / 'repositories' / key


def default_db(root):
    return repo_cache(root) / 'index.sqlite'


def model_path():
    return cache_home() / 'models' / 'coderank'


def venv_python():
    return APP / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
