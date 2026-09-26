"""Root conftest.py - makes the application module importable under pytest.

The production code lives in ``sonarr-tagger/``, a hyphenated directory that is
not a valid package name. This shim puts it (and the repo root) on ``sys.path``
so tests can simply ``import main``.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(REPO_ROOT, "sonarr-tagger")

for path in (REPO_ROOT, APP_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)
