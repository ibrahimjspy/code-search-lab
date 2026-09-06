#!/usr/bin/env bash
# POSIX convenience wrapper; setup.py is the portable entry point.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 setup.py "$@"
