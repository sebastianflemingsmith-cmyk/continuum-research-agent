#!/usr/bin/env python3
"""Run the original regression suites in a temporary runtime, without network."""
from pathlib import Path
import subprocess
import sys
import tempfile

from workspace import ROOT, create


def main():
    with tempfile.TemporaryDirectory(prefix="continuum-tests-") as folder:
        runtime = create(Path(folder) / "Agent", snapshot=True)
        for entry in ("reader/test/run_tests.py", "approvals/test/test_approvals.py"):
            result = subprocess.run([sys.executable, str(ROOT / "tools/offline.py"), str(runtime / entry)], cwd=runtime)
            if result.returncode:
                return result.returncode
    print("Both regression suites passed; source and example data were not modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
