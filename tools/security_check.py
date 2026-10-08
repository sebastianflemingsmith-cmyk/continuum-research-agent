#!/usr/bin/env python3
"""Scan publishable files (including XLSX/gzip contents) without printing matches.

A regression guard, not proof that arbitrary confidential prose is absent.
Git mode includes tracked and nonignored files; local .local/ workspaces are excluded.
"""
import argparse
import gzip
import io
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "credential pattern": re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "personal machine path": re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+/"),
}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
SOURCE_DOMAINS = {"amazon.com", "abc.xyz", "we-worldwide.com", "nvidia.com", "oracle.com"}
SAFE_DOMAINS = {"example.com", "users.noreply.github.com"}
EXCLUDED = {".git", ".local", ".venv", "__pycache__"}


def private_name(name):
    p = Path(name)
    lower = p.name.lower()
    return (lower == "config.json" or lower == ".env" or lower.startswith(".env.")
            or p.suffix.lower() in (".pem", ".key")
            or lower in ("credentials", "credentials.json", "credentials.yml", "credentials.yaml"))


def content_parts(name, data):
    if name.endswith(".xlsx"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.namelist():
                yield name + "::" + member, archive.read(member)
    elif name.endswith(".gz"):
        yield name + "::decompressed", gzip.decompress(data)
    else:
        yield name, data


def scan(name, data):
    problems = []
    for label, raw in content_parts(name, data):
        text = raw.decode("utf-8", errors="replace")
        for reason, pattern in PATTERNS.items():
            if pattern.search(text):
                problems.append((label, reason))
        # GitHub's generated PR test merges use this exact non-personal identity.
        domains = {match.group(1).lower() for match in EMAIL.finditer(text)
                   if match.group(0).lower() != "noreply@github.com"}
        allowed = set(SAFE_DOMAINS)
        if "/documents/" in name and name.endswith(".txt"):
            allowed |= SOURCE_DOMAINS
        if domains - allowed:
            problems.append((label, "email outside approved example/source domains"))
    return problems


def selected_paths(root):
    if (root / ".git").exists():
        raw = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root)
        return sorted(set(p.decode() for p in raw.split(b"\0") if p))
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                  if p.is_file() and not any(part in EXCLUDED for part in p.relative_to(root).parts))


def history_problems(root):
    problems = []
    refs = subprocess.check_output(["git", "rev-list", "--all", "--objects"], cwd=root, text=True)
    for line in refs.splitlines():
        oid, _, name = line.partition(" ")
        kind = subprocess.check_output(["git", "cat-file", "-t", oid], cwd=root, text=True).strip()
        if kind not in ("commit", "tag", "blob"):
            continue
        data = subprocess.check_output(["git", "cat-file", kind, oid], cwd=root)
        label = name or ("git-" + kind)
        if name and private_name(name):
            problems.append((label, "private filename in history"))
        for path, reason in scan(label, data):
            problems.append(("history:" + path, reason))
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--history", action="store_true", help="also inspect every reachable Git blob and commit identity")
    args = ap.parse_args()
    paths = selected_paths(ROOT)
    problems = []
    for name in paths:
        path = ROOT / name
        if private_name(name):
            problems.append((name, "private filename selected for publication"))
            continue
        if path.is_symlink():
            problems.append((name, "symlink selected for publication"))
            continue
        problems.extend(scan(name, path.read_bytes()))
    if args.history:
        problems.extend(history_problems(ROOT))
    if problems:
        for name, reason in sorted(set(problems)):
            print("FAIL:", name, "—", reason)
        return 1
    print("Security regression scan passed:", len(paths), "selected files; ZIP/gzip contents checked."
          + (" Reachable Git history checked." if args.history else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
