#!/usr/bin/env python3
"""Replay saved extraction and build the dated approved review without any API calls."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile

from offline import block_network
from workspace import ROOT, create


def run(output):
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise ValueError("Output already exists; choose a new --out folder.")
    if ROOT == output or ROOT in output.parents:
        if ROOT / ".local" not in output.parents:
            raise ValueError("Within the checkout, use an output below .local/.")
    block_network()
    with tempfile.TemporaryDirectory(prefix="continuum-demo-") as folder:
        runtime = create(Path(folder) / "Agent", snapshot=True)
        sys.path.insert(0, str(runtime / "reader"))
        import reader_text as reader
        from lib import documents
        # Exact documents and model answers from one recorded run, not a new AI call.
        cache = Path(folder) / "cache"
        cache.mkdir()
        import shutil
        for path in (runtime / "reader/documents").iterdir():
            if "_2026-09-28_" in path.name:
                shutil.copyfile(path, cache / path.name)
        documents.CACHE_DIR = str(cache)
        transcript = io.StringIO()
        output.mkdir(parents=True)
        with contextlib.redirect_stdout(transcript):
            replay = reader.main(["--from-cache", "--without-handoff", "--answers",
                                  str(runtime / "reader/test/fixtures/answers_full_run_2026-09-28"),
                                  "--out", str(output / "replay")])
        if replay is None:
            raise RuntimeError("The saved-answer replay did not run.")
        spec = importlib.util.spec_from_file_location("demo_review", runtime / "review/build.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        review, notes = builder.build(**builder.read_inputs(**builder.default_paths(str(runtime))))
        problems = builder.schema_problems(review)
        if problems:
            raise RuntimeError("Review schema failed: " + str(problems))
        (output / "review.json").write_text(builder.to_json(review), encoding="utf-8")
        summary = {"mode": "offline historical demonstration", "snapshot_built": review["built"],
                   "counts": review["counts"], "network_calls": 0,
                   "approval_source": "curated recorded decisions; replay proposals are not automatically approved"}
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        (output / "README.txt").write_text(
            "DATED DEMONSTRATION, NOT A LIVE CHECK\n\n"
            "replay/: saved 28 September 2026 documents and model answers processed by the real reader.\n"
            "review.json: the real builder applied to the curated historical approvals snapshot.\n"
            "Replay findings are not automatically approved. No model or source service was called.\n", encoding="utf-8")
    print("Offline demonstration complete:", output)
    print("Figures:", review["counts"]["figures"], "| Table rows:", review["counts"]["rows"])
    print("Historical snapshot:", review["built"], "| Network/API calls: 0")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / ".local/demo"))
    args = ap.parse_args()
    try:
        run(args.out)
    except ValueError as exc:
        ap.error(str(exc))
