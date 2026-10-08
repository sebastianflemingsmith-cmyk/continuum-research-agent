#!/usr/bin/env python3
"""
Rebuild the open list: Open proposals.md and proposals_open.jsonl, from proposed.jsonl (which is never changed).

    python3 Agent/reader/open_proposals.py

Part B does this at the end of every run; run it by hand after Part A. How proposals are merged: lib/openlist.py.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "watcher"))
sys.path.insert(0, HERE)
from lib import openlist  # noqa: E402

if __name__ == "__main__":
    openlist.rebuild()
