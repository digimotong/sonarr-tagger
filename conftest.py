"""Root conftest.py - makes the application module importable under pytest.

The production code lives in ``sonarr-tagger/`` (a hyphenated directory, which is
not a valid Python package name and therefore cannot be imported normally). This
shim puts that directory on ``sys.path`` so tests can simply ``import main``.

It also ensures the repository root is importable so the ``tests`` package
resolves consistently regardless of the directory pytest is invoked from.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(REPO_ROOT, "sonarr-tagger")

for path in (REPO_ROOT, APP_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)
