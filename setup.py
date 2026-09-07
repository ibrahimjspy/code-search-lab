#!/usr/bin/env python3
"""Optional dependency setup usable from Python on Windows, macOS and Linux."""
import argparse
import os
import shutil
import subprocess
import sys
import venv
from paths import APP, model_path, venv_python


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--typescript', action='store_true', help='Install the optional TS/JS parser')
    parser.add_argument('--embeddings', action='store_true', help='Install the embedding environment and public model')
    parser.add_argument('--service', action='store_true', help='Install file watching, local service and MCP dependencies')
    args = parser.parse_args()
    if args.typescript:
        npm = shutil.which('npm')
        if not npm:
            parser.error('Install Node.js and npm before requesting the TypeScript parser.')
        command = [npm, 'ci', '--ignore-scripts', '--no-audit', '--no-fund']
        # Windows npm is a command script; invoke through its command interpreter.
        if os.name == 'nt':
            command = [os.environ.get('COMSPEC', 'cmd.exe'), '/c', *command]
        subprocess.run(command, cwd=APP, check=True)
    if args.embeddings or args.service:
        if not venv_python().exists():
            venv.EnvBuilder(with_pip=True, system_site_packages=False).create(APP / '.venv')
        python = venv_python()
        if args.service:
            subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(APP / 'requirements-service.txt')], check=True)
    if args.embeddings:
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(APP / 'requirements.txt')], check=True)
        code = """from huggingface_hub import snapshot_download
import sys
snapshot_download('nomic-ai/CodeRankEmbed', revision='3c4b60807d71f79b43f3c4363786d9493691f8b1',
                  local_dir=sys.argv[1], allow_patterns=['*.py','*.json','*.txt','*.safetensors'], token=False)
"""
        subprocess.run([str(python), '-c', code, str(model_path())], check=True)
    print('Ready. Run: python codesearch search "your question" --repo path/to/project')


if __name__ == '__main__':
    main()
