#!/usr/bin/env python3
"""Tests for the public checkout's privacy and offline-workspace boundary."""
from pathlib import Path
import socket
import gzip
import io
import zipfile
import tempfile
import unittest
from unittest.mock import patch

import offline
import security_check
import workspace


class WorkspaceBoundary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="continuum-boundary-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "source"
        self.root.mkdir()
        for module in workspace.MODULES:
            (self.root / module).mkdir()
            (self.root / module / "entry.py").write_text("# public code\n")
        self.snapshot = self.root / "examples/snapshot"
        self.snapshot.mkdir(parents=True)
        (self.snapshot / "Watch list - The AI Lose Lose Race.xlsx").write_bytes(b"public workbook fixture")
        self.root_patch = patch.object(workspace, "ROOT", self.root)
        self.snapshot_patch = patch.object(workspace, "SNAPSHOT", self.snapshot)
        self.root_patch.start()
        self.snapshot_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.snapshot_patch.stop)

    def test_dirty_source_credentials_are_never_copied(self):
        folder = self.root / "reader/test/fixtures"
        folder.mkdir(parents=True)
        for name in ("config.json", ".env", ".env.local", "service.key", "service.pem", "credentials.json"):
            (folder / name).write_text("synthetic-private-sentinel")
        (self.root / "reader/config.json").write_text("synthetic-private-sentinel")
        (self.root / "reader/config.example.json").write_text('{"deepseek_api_key":""}')
        destination = workspace.create(self.base / "runtime")
        for p in destination.rglob("*"):
            if p.is_file():
                self.assertNotIn(b"synthetic-private-sentinel", p.read_bytes())
        self.assertTrue((destination / "reader/config.example.json").exists())

    def test_live_workspace_has_no_inherited_history(self):
        for relative in ("reader/decisions.jsonl", "reader/proposed.jsonl", "reader/runs.jsonl", "watcher/state.json", "watcher/handoff.json", "review/paper_differs.json"):
            p = self.snapshot / relative
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("historical-sentinel")
        destination = workspace.create(self.base / "runtime")
        for name in ("decisions.jsonl", "proposed.jsonl", "runs.jsonl"):
            self.assertEqual((destination / "reader" / name).read_text(), "")
        self.assertFalse((destination / "watcher/state.json").exists())
        self.assertFalse((destination / "watcher/handoff.json").exists())
        self.assertFalse((destination / "review/paper_differs.json").exists())

    def test_existing_destination_is_not_changed(self):
        destination = self.base / "runtime"
        destination.mkdir()
        private = destination / "config.json"
        private.write_text("original-sentinel")
        with self.assertRaises(ValueError):
            workspace.create(destination)
        self.assertEqual(private.read_text(), "original-sentinel")

    def test_source_tree_only_allows_ignored_runtime_destination(self):
        for destination in (self.root, self.root / "reader/runtime", self.root / "new-folder"):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                workspace.create(destination)
        destination = workspace.create(self.root / ".local/live")
        self.assertTrue((destination / "reader/entry.py").is_file())

    def test_snapshot_credential_files_fail_closed(self):
        for i, name in enumerate(("config.json", ".env", ".env.production", "private.key", "private.pem", "credentials.json")):
            private = self.snapshot / name
            private.write_text("synthetic-private-sentinel")
            try:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    workspace.create(self.base / ("runtime-" + str(i)), snapshot=True)
            finally:
                private.unlink()

    def test_symlink_in_source_is_refused(self):
        secret = self.base / "private-data"
        secret.write_text("synthetic-private-sentinel")
        (self.root / "reader/leak.py").symlink_to(secret)
        with self.assertRaises(ValueError):
            workspace.create(self.base / "runtime")


class OfflineBoundary(unittest.TestCase):
    def test_dns_tcp_udp_are_blocked(self):
        functions = ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "create_connection")
        methods = ("connect", "connect_ex", "sendto")
        saved = {name: getattr(socket, name) for name in functions}
        saved_methods = {name: getattr(socket.socket, name) for name in methods}
        try:
            offline.block_network()
            for name in functions:
                with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "Network access is disabled"):
                    getattr(socket, name)("offline.example.com", 443)
            for name in methods:
                with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "Network access is disabled"):
                    getattr(socket.socket, name)(None, ("127.0.0.1", 443))
        finally:
            for name, function in saved.items():
                setattr(socket, name, function)
            for name, function in saved_methods.items():
                setattr(socket.socket, name, function)


class PublicationScanner(unittest.TestCase):
    def test_detects_credential_without_printing_value(self):
        token = "sk-" + "a" * 32
        findings = security_check.scan("example.json", token.encode())
        self.assertEqual(findings, [("example.json", "credential pattern")])
        self.assertNotIn(token, str(findings))

    def test_scans_compressed_source_data(self):
        private_path = "/" + "Users" + "/private-person/documents/"
        findings = security_check.scan("fixture.json.gz", gzip.compress(private_path.encode()))
        self.assertEqual(findings[0][1], "personal machine path")

    def test_scans_workbook_metadata(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("docProps/core.xml", "person" + "@" + "private-mail.invalid")
        findings = security_check.scan("fixture.xlsx", buf.getvalue())
        self.assertEqual(findings[0][0], "fixture.xlsx::docProps/core.xml")
        self.assertIn("email", findings[0][1])

    def test_source_contact_allowlist_is_limited_to_source_documents(self):
        contact = b"press" + b"@" + b"nvidia.com"
        self.assertEqual(security_check.scan("examples/snapshot/reader/documents/release.txt", contact), [])
        self.assertTrue(security_check.scan("notes.txt", contact))


if __name__ == "__main__":
    unittest.main(verbosity=2)
