"""Tests for afs-snapshot-break-lease.py (standard library only).

Run from the tool folder:  python -m unittest discover -s tests -v
"""
import base64
import contextlib
import csv
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mock_server  # noqa: E402

SCRIPT = HERE.parent / "afs-snapshot-break-lease.py"
spec = importlib.util.spec_from_file_location("afs", SCRIPT)
afs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(afs)

KEY = base64.b64encode(bytes(range(64))).decode()
OLD_LEASED = "2020-01-01T00:00:00.0000000Z"
OLD_FREE = "2020-02-01T00:00:00.0000000Z"
OLD_BACKUP = "2020-03-01T00:00:00.0000000Z"
NEW_LEASED = "2099-01-01T00:00:00.0000000Z"


class ScriptTestCase(unittest.TestCase):
    def setUp(self):
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        os.environ["no_proxy"] = "127.0.0.1,localhost"
        self.home = tempfile.TemporaryDirectory()
        self._old_home = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "APPDATA")}
        os.environ["HOME"] = os.environ["USERPROFILE"] = os.environ["APPDATA"] = self.home.name
        self.mock = mock_server.MockAzureFiles(KEY).start()
        self._sleep = afs.time.sleep
        afs.time.sleep = lambda s: None  # no real backoff in tests

    def tearDown(self):
        afs.time.sleep = self._sleep
        self.mock.stop()
        for k, v in self._old_home.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import logging
        for h in list(logging.getLogger().handlers):
            h.close()
            logging.getLogger().removeHandler(h)
        logging.getLogger().addHandler(logging.NullHandler())
        self.home.cleanup()

    def run_script(self, *extra, key=KEY, stdin=None):
        argv = ["--auth", "1", "--account", "devacct", "--account-url", self.mock.url, "--non-interactive"]
        if key:
            argv += ["--key", key]
        argv += list(extra)
        out = io.StringIO()
        old_stdin = sys.stdin
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out):
                try:
                    code = afs.main(argv)
                except SystemExit as e:
                    code = e.code
        finally:
            sys.stdin = old_stdin
        self.output = out.getvalue()
        return code

    def broken(self):
        return [(r["path"].strip("/"), r["query"]["sharesnapshot"]) for r in self.mock.calls("PUT")]

    def deleted(self):
        return [(r["path"].strip("/"), r["query"]["sharesnapshot"]) for r in self.mock.calls("DELETE")]

    def state(self, share, snap):
        return self.mock.shares[share]["snapshots"].get(snap, {}).get("lease")


class SignatureTests(unittest.TestCase):
    """Vectors produced by azure-storage-file-share's SharedKeyCredentialPolicy."""
    DATE = "Fri, 25 Sep 2026 12:00:00 GMT"
    VECTORS = [
        ("GET", "/", {"comp": "list", "include": "snapshots,metadata", "prefix": "data"}, {},
         "SharedKey devacct:1B2gb6JBm4niVQMy7cTE/xSLa44wx+rtPJGIf9YfMWM="),
        ("PUT", "/data", {"comp": "lease", "restype": "share", "sharesnapshot": OLD_LEASED},
         {"x-ms-lease-action": "break", "Content-Length": "0", "Content-Type": "application/octet-stream"},
         "SharedKey devacct:ryCdVZZNpS7RqSIA+VR4Uq6W1xu01vTp4nPKbn1VL40="),
        ("DELETE", "/data", {"restype": "share", "sharesnapshot": OLD_LEASED},
         {"Content-Length": "0", "Content-Type": "application/octet-stream"},
         "SharedKey devacct:NXC/+e9y+uaT/CaCRg56Jkp6Au1cRxNrBlJ1/QJpEqU="),
    ]

    def test_matches_azure_sdk(self):
        client = afs.FileShareRestClient("devacct", "core.windows.net", key=KEY)
        for method, path, query, headers, expected in self.VECTORS:
            h = {"x-ms-version": "2024-11-04", "x-ms-date": self.DATE, **headers}
            with self.subTest(method=method):
                self.assertEqual(client._sign(method, path, query, h), expected)


class HelperTests(unittest.TestCase):
    def test_parse_timestamp(self):
        self.assertEqual(afs.parse_snapshot_timestamp(OLD_LEASED).year, 2020)
        self.assertEqual(afs.parse_snapshot_timestamp("2020-01-01T00:00:00Z").month, 1)

    def test_backup_hint(self):
        leased = {"status": "locked", "state": "leased", "duration": "infinite", "metadata": {}}
        self.assertEqual(afs.backup_hint({**leased, "metadata": {"AzureBackupX": "1"}}, False), "Yes")
        self.assertEqual(afs.backup_hint(leased, True), "Likely")
        self.assertEqual(afs.backup_hint(leased, False), "-")


class ScriptTests(ScriptTestCase):
    def test_dry_run_changes_nothing(self):
        self.assertEqual(self.run_script("--share", "data", "--days", "30", "--dry-run"), 0)
        self.assertEqual(self.broken(), [])
        self.assertIn("Dry-run", self.output)
        self.assertIn("Likely", self.output)
        self.assertIn("Azure Backup metadata", self.output)

    def test_breaks_only_old_leased_snapshots_of_exact_share(self):
        self.assertEqual(self.run_script("--share", "data", "--days", "30", "--yes"), 0)
        self.assertEqual(sorted(self.broken()), [("data", OLD_LEASED), ("data", OLD_BACKUP)])
        self.assertEqual(self.state("data-archive", OLD_LEASED), "leased")
        self.assertEqual(self.state("data", NEW_LEASED), "leased")

    def test_non_interactive_without_yes_changes_nothing(self):
        self.assertEqual(self.run_script("--share", "data", "--days", "30"), 0)
        self.assertEqual(self.broken(), [])

    def test_specific_snapshot(self):
        self.assertEqual(self.run_script("--share", "data", "--snapshot", NEW_LEASED, "--yes"), 0)
        self.assertEqual(self.broken(), [("data", NEW_LEASED)])

    def test_missing_snapshot_warns(self):
        self.assertEqual(self.run_script("--share", "data", "--snapshot", "2021-01-01T00:00:00.0000000Z", "--yes"), 0)
        self.assertIn("was not found", self.output)
        self.assertEqual(self.broken(), [])

    def test_all_shares(self):
        self.assertEqual(self.run_script("--all-shares", "--days", "30", "--yes"), 0)
        self.assertEqual(sorted(self.broken()),
                         [("data", OLD_LEASED), ("data", OLD_BACKUP), ("data-archive", OLD_LEASED)])

    def test_share_not_found(self):
        self.assertEqual(self.run_script("--share", "nope", "--days", "30", "--yes"), 1)
        self.assertIn("was not found", self.output)

    def test_delete(self):
        self.assertEqual(self.run_script("--share", "data", "--days", "30", "--delete", "--yes"), 0)
        self.assertEqual(sorted(self.deleted()), [("data", OLD_LEASED), ("data", OLD_FREE), ("data", OLD_BACKUP)])
        self.assertEqual(sorted(self.mock.shares["data"]["snapshots"]), [NEW_LEASED])

    def test_delete_requires_typed_confirmation(self):
        argv = ["--auth", "1", "--account", "devacct", "--account-url", self.mock.url, "--key", KEY,
                "--share", "data", "--days", "30", "--delete"]
        answers = iter(["y", "nope"])
        import builtins
        real = builtins.input
        builtins.input = lambda prompt="": next(answers)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(afs.main(argv), 0)
        finally:
            builtins.input = real
        self.assertEqual(self.deleted(), [])
        self.assertEqual(len(self.broken()), 2)

    def test_retries_transient_errors(self):
        self.assertEqual(self.run_script("--share", "data", "--snapshot", OLD_FREE, "--dry-run"), 0)
        baseline = len(self.mock.calls("GET"))
        self.mock.requests.clear()
        self.mock.faults = [("GET", 503), ("PUT", 500)]
        self.assertEqual(self.run_script("--share", "data", "--snapshot", OLD_LEASED, "--yes"), 0)
        self.assertEqual(len(self.mock.calls("GET")), baseline + 1)
        self.assertEqual(len(self.mock.calls("PUT")), 2)
        self.assertEqual(self.state("data", OLD_LEASED), "broken")

    def test_partial_failure_exit_code(self):
        self.mock.faults = [("PUT", 409)]  # not retryable
        self.assertEqual(self.run_script("--share", "data", "--days", "30", "--yes"), 2)

    def test_wrong_key(self):
        wrong = base64.b64encode(b"x" * 64).decode()
        self.assertEqual(self.run_script("--share", "data", "--days", "30", "--yes", key=wrong), 1)
        self.assertIn("Authentication failed", self.output)

    def test_invalid_key(self):
        self.assertEqual(self.run_script("--share", "data", "--days", "30", key="not base64!"), 1)

    def test_key_from_environment(self):
        os.environ["AZURE_STORAGE_KEY"] = KEY
        try:
            self.assertEqual(self.run_script("--share", "data", "--days", "30", "--dry-run", key=None), 0)
        finally:
            del os.environ["AZURE_STORAGE_KEY"]

    def test_missing_args_non_interactive(self):
        self.assertEqual(self.run_script("--share", "data"), 1)
        self.assertIn("--days or --snapshot", self.output)

    def test_mutually_exclusive_args(self):
        self.assertEqual(self.run_script("--share", "data", "--all-shares", "--days", "3"), 1)
        self.assertEqual(self.run_script("--share", "data", "--days", "3", "--snapshot", OLD_LEASED), 1)

    def test_csv_report(self):
        path = os.path.join(self.home.name, "r.csv")
        self.assertEqual(self.run_script("--share", "data", "--days", "30", "--yes", "--report", path), 0)
        with open(path, newline="", encoding="utf-8") as f:
            rows = {r["snapshot"]: r for r in csv.DictReader(f)}
        self.assertEqual(rows[OLD_LEASED]["result"], "SUCCESS")
        self.assertEqual(rows[OLD_BACKUP]["backup"], "Yes")
        self.assertEqual(rows[NEW_LEASED]["selected"], "False")

    def test_json_report(self):
        path = os.path.join(self.home.name, "r.json")
        self.assertEqual(self.run_script("--share", "data", "--days", "30", "--dry-run", "--report", path), 0)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertTrue(data["dry_run"])
        self.assertEqual(len(data["snapshots"]), 4)

    def test_bearer_token(self):
        client = afs.FileShareRestClient("devacct", "core.windows.net", token=mock_server.TOKEN, account_url=self.mock.url)
        entries = list(client.list_snapshots("data"))
        self.assertEqual(len([e for e in entries if e["snapshot"]]), 4)
        client.break_lease("data", OLD_LEASED)
        self.assertEqual(self.state("data", OLD_LEASED), "broken")


if __name__ == "__main__":
    unittest.main()
