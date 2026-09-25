"""In-memory mock of the Azure Files REST endpoints used by the lease breaker scripts.

Verifies Shared Key signatures (or a fixed Bearer token), supports paging, lease break,
snapshot delete and fault injection. Used by the Python tests and the PowerShell CI test.

Run standalone:  python mock_server.py --port 8765 --key <base64-key> [--requests-log file.jsonl]
"""
import argparse
import base64
import hashlib
import hmac
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ACCOUNT = "devacct"
TOKEN = "test-token"
PAGE_SIZE = 3


def default_shares():
    """share name -> {'metadata': {...}, 'snapshots': {timestamp: {'lease': state, 'metadata': {...}}}}"""
    return {
        "data": {
            "metadata": {"AzureBackupProtected": "true"},
            "snapshots": {
                "2020-01-01T00:00:00.0000000Z": {"lease": "leased", "metadata": {}},
                "2020-02-01T00:00:00.0000000Z": {"lease": "available", "metadata": {}},
                "2020-03-01T00:00:00.0000000Z": {"lease": "leased", "metadata": {"AzureBackupSnapshot": "1"}},
                "2099-01-01T00:00:00.0000000Z": {"lease": "leased", "metadata": {}},
            },
        },
        "data-archive": {
            "metadata": {},
            "snapshots": {"2020-01-01T00:00:00.0000000Z": {"lease": "leased", "metadata": {}}},
        },
        "empty": {"metadata": {}, "snapshots": {}},
    }


def sign(key_b64, method, path, query, headers):
    key = base64.b64decode(key_b64)
    h = {k.lower(): v for k, v in headers.items()}
    length = h.get("content-length", "")
    std = [h.get("content-encoding", ""), h.get("content-language", ""), "" if length in ("", "0") else length,
           h.get("content-md5", ""), h.get("content-type", ""), "", h.get("if-modified-since", ""), h.get("if-match", ""),
           h.get("if-none-match", ""), h.get("if-unmodified-since", ""), h.get("range", "")]
    ms = sorted((k, v.strip()) for k, v in h.items() if k.startswith("x-ms-"))
    res = f"/{ACCOUNT}{path}" + "".join(f"\n{k.lower()}:{query[k]}" for k in sorted(query, key=str.lower))
    sts = method + "\n" + "\n".join(std) + "\n" + "".join(f"{k}:{v}\n" for k, v in ms) + res
    return "SharedKey " + ACCOUNT + ":" + base64.b64encode(hmac.new(key, sts.encode(), hashlib.sha256).digest()).decode()


class MockAzureFiles:
    def __init__(self, key, port=0, requests_log=None):
        self.key = key
        self.shares = default_shares()
        self.requests = []
        self.faults = []  # list of (method, status) injected before normal handling
        self.requests_log = requests_log
        self.lock = threading.Lock()
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, status, body=b"", headers=None):
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _error(self, status, code):
                body = f'<?xml version="1.0" encoding="utf-8"?><Error><Code>{code}</Code><Message>{code}</Message></Error>'.encode()
                self._reply(status, body, {"x-ms-error-code": code, "Content-Type": "application/xml"})

            def _handle(self):
                u = urllib.parse.urlparse(self.path)
                query = {k: v[0] for k, v in urllib.parse.parse_qs(u.query, keep_blank_values=True).items()}
                path = urllib.parse.unquote(u.path)
                with mock.lock:
                    entry = {"method": self.command, "path": path, "query": query}
                    mock.requests.append(entry)
                    if mock.requests_log:
                        with open(mock.requests_log, "a", encoding="utf-8") as f:
                            f.write(json.dumps(entry) + "\n")
                    for idx, (m, status) in enumerate(mock.faults):
                        if m == self.command:
                            mock.faults.pop(idx)
                            return self._error(status, "ServerBusy")

                auth = self.headers.get("Authorization", "")
                if auth.startswith("Bearer "):
                    if auth != f"Bearer {TOKEN}" or self.headers.get("x-ms-file-request-intent") != "backup":
                        return self._error(401, "InvalidAuthenticationInfo")
                else:
                    hdrs = {k: v for k, v in self.headers.items() if k.lower() != "authorization"}
                    if auth != sign(mock.key, self.command, path, query, hdrs):
                        return self._error(403, "AuthenticationFailed")

                # The Azure SDK puts the account in the path for non-Azure endpoints (http://host/devacct/share).
                share = path.strip("/")
                if share == ACCOUNT or share.startswith(ACCOUNT + "/"):
                    share = share[len(ACCOUNT):].strip("/")
                with mock.lock:
                    if self.command == "GET" and query.get("comp") == "list" and not share:
                        return self._list(query)
                    if share not in mock.shares:
                        return self._error(404, "ShareNotFound")
                    snaps = mock.shares[share]["snapshots"]
                    snap = query.get("sharesnapshot")
                    if snap not in snaps:
                        return self._error(404, "ShareSnapshotNotFound")
                    if self.command == "PUT" and query.get("comp") == "lease" and self.headers.get("x-ms-lease-action") == "break":
                        if snaps[snap]["lease"] != "leased":
                            return self._error(409, "LeaseNotPresentWithShareOperation")
                        snaps[snap]["lease"] = "broken"
                        return self._reply(202, headers={"x-ms-lease-time": "0"})
                    if self.command == "DELETE" and query.get("restype") == "share":
                        if snaps[snap]["lease"] == "leased":
                            return self._error(412, "LeaseIdMissing")
                        del snaps[snap]
                        return self._reply(202)
                return self._error(400, "UnsupportedOperation")

            def _list(self, query):
                prefix = query.get("prefix", "")
                include = query.get("include", "").lower()
                items = []
                for name in sorted(mock.shares):
                    if not name.startswith(prefix):
                        continue
                    s = mock.shares[name]
                    items.append((name, None, "available", s["metadata"]))
                    if "snapshots" in include:
                        for ts in sorted(s["snapshots"]):
                            sn = s["snapshots"][ts]
                            items.append((name, ts, sn["lease"], sn["metadata"]))
                start = int(query.get("marker") or 0)
                page = items[start:start + PAGE_SIZE]
                next_marker = str(start + PAGE_SIZE) if start + PAGE_SIZE < len(items) else ""
                xml = ['<?xml version="1.0" encoding="utf-8"?><EnumerationResults><Shares>']
                for name, ts, lease, meta in page:
                    status = "locked" if lease == "leased" else "unlocked"
                    xml.append(f"<Share><Name>{name}</Name>")
                    if ts:
                        xml.append(f"<Snapshot>{ts}</Snapshot>")
                    xml.append(f"<Properties><Last-Modified>Wed, 01 Jan 2020 00:00:00 GMT</Last-Modified><Etag>\"0x1\"</Etag>"
                               f"<Quota>100</Quota><LeaseStatus>{status}</LeaseStatus><LeaseState>{lease}</LeaseState>")
                    if lease == "leased":
                        xml.append("<LeaseDuration>infinite</LeaseDuration>")
                    xml.append("</Properties>")
                    if "metadata" in include:
                        xml.append("<Metadata>" + "".join(f"<{k}>{v}</{k}>" for k, v in meta.items()) + "</Metadata>")
                    xml.append("</Share>")
                xml.append(f"</Shares><NextMarker>{next_marker}</NextMarker></EnumerationResults>")
                return self._reply(200, "".join(xml).encode(), {"Content-Type": "application/xml"})

            do_GET = do_PUT = do_DELETE = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self.server.server_port
        self.url = f"http://127.0.0.1:{self.port}"

    def start(self):
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def calls(self, method):
        return [r for r in self.requests if r["method"] == method]


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--key", required=True)
    p.add_argument("--requests-log")
    a = p.parse_args()
    m = MockAzureFiles(a.key, a.port, a.requests_log)
    print(f"Mock Azure Files listening on {m.url}", flush=True)
    m.server.serve_forever()
