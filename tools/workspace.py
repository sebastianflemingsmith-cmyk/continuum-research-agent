#!/usr/bin/env python3
"""Create an isolated runtime from code and an explicitly curated data snapshot.

The checkout is the source, never a live research workspace. Existing destinations
are refused so that an operator's credentials and decisions cannot be overwritten.
"""
import argparse
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "examples" / "snapshot"
MODULES = ("watcher", "reader", "approvals", "review", "runner")


def private_filename(path):
    name = path.name.lower()
    return (name == "config.json" or name == ".env" or name.startswith(".env.")
            or path.suffix.lower() in (".pem", ".key")
            or name in ("credentials", "credentials.json", "credentials.yml", "credentials.yaml"))


def create(destination, snapshot=False):
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise ValueError("Destination already exists; choose a new workspace.")
    # Runtime files inside this checkout are confined to the ignored .local folder.
    if ROOT == destination or ROOT in destination.parents:
        if ROOT / ".local" not in destination.parents:
            raise ValueError("Within the checkout, use a destination below .local/.")
    destination.mkdir(parents=True)
    for module in MODULES:
        src = ROOT / module
        for path in src.rglob("*"):
            rel = path.relative_to(ROOT)
            if path.is_symlink():
                raise ValueError("Symlinks are not supported in the source tree.")
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            # A developer may have local runtime outputs: never copy those, or keys.
            if path.suffix == ".py" or path.name in ("README.md", "config.example.json") or "fixtures" in rel.parts or rel.as_posix() == "approvals/Figures 2026-10-04.csv":
                if private_filename(path):
                    continue
                out = destination / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, out)
    workbook = "Watch list - The AI Lose Lose Race.xlsx"
    if snapshot:
        for path in SNAPSHOT.rglob("*"):
            if path.is_symlink():
                raise ValueError("Symlinks are not supported in the example snapshot.")
            if path.is_file():
                if private_filename(path):
                    raise ValueError("A snapshot must never contain live configuration.")
                out = destination / path.relative_to(SNAPSHOT)
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, out)
    else:
        shutil.copyfile(SNAPSHOT / workbook, destination / workbook)
        # Empty state, so the operator starts with no inherited approvals/bookmarks.
        for name in ("proposed.jsonl", "decisions.jsonl", "runs.jsonl"):
            (destination / "reader" / name).write_text("", encoding="utf-8")
    for name in ("reader/documents", "reader/reports", "approvals/sheets", "watcher/reports"):
        (destination / name).mkdir(parents=True, exist_ok=True)
    return destination


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="new runtime folder, e.g. .local/live")
    args = ap.parse_args()
    try:
        dest = create(args.out)
    except ValueError as exc:
        ap.error(str(exc))
    print("Created a fresh runtime:", dest)
    print("No credentials, historical approvals or watcher bookmarks were copied.")
    print("See docs/live-use.md for configuration and commands.")


if __name__ == "__main__":
    main()
