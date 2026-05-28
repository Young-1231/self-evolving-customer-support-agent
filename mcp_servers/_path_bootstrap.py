"""Make ``seagent.mcp`` importable when the server is launched as a
subprocess from an arbitrary cwd (e.g. by tests or scripts).

We do not want to require ``pip install -e .`` for the demo to run.
"""
from __future__ import annotations

import os
import sys


def ensure_seagent_on_path() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    src_dir = os.path.join(repo_root, "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
