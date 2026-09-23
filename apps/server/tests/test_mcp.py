"""End to end over MCP: what Claude actually sees from the contacts server.

    uv run --with cryptography python e2e_contacts_mcp.py [server.py]

A mock CardDAV server (plain HTTP, 127.0.0.1) holds two "Max Mustermann".
A second one speaks HTTPS with a self-signed certificate.
"""
import datetime as dt
import http.server
import ipaddress
import json
import os
import socketserver
import ssl
import subprocess
import sys
import threading
from pathlib import Path
from xml.sax.saxutils import escape

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

SERVER = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[1] / "server.py")
HERE = Path(__file__).parent
socketserver.BaseServer.handle_error = lambda *a: None

CARDS = {
    "/carddav/u/a/1.vcf": "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:UID-1\r\nFN:Max Mustermann\r\nTEL:0170 111\r\nEND:VCARD\r\n",
    "/carddav/u/a/2.vcf": "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:UID-2\r\nFN:Max Mustermann\r\nEMAIL:max@work.test\r\nEND:VCARD\r\n",
}
seen = []
STATE = {}
MS = '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">{}</d:multistatus>'


class Dav(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="application/xml"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PROPFIND(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        seen.append(("PROPFIND", self.path))
        if self.path == "/carddav/":
            r = ('<d:response><d:href>/carddav/</d:href><d:propstat><d:prop><d:current-user-principal>'
                 '<d:href>/carddav/u/</d:href></d:current-user-principal></d:prop>'
                 '<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>')
        elif self.headers.get("Depth") == "0":
            r = ('<d:response><d:href>/carddav/u/</d:href><d:propstat><d:prop><c:addressbook-home-set>'
                 '<d:href>/carddav/u/</d:href></c:addressbook-home-set></d:prop>'
                 '<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>')
        else:
            r = ('<d:response><d:href>/carddav/u/a/</d:href><d:propstat><d:prop><d:resourcetype>'
                 '<d:collection/><c:addressbook/></d:resourcetype><d:displayname>Kontakte</d:displayname>'
                 '</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>')
        self._send(207, MS.format(r).encode())

    def do_REPORT(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        seen.append(("REPORT", self.path))
        r = "".join(f'<d:response><d:href>{h}</d:href><d:propstat><d:prop><d:getetag>"{h}"</d:getetag>'
                    f"<c:address-data>{escape(v)}</c:address-data></d:prop>"
                    "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
                    for h, v in CARDS.items())
        self._send(207, MS.format(r).encode())

    def do_GET(self):
        seen.append(("GET", self.path))
        if STATE.get("session_over"):  # DSM answers with its login page, HTTP 200
            return self._send(200, b"<html>login</html>", "text/html")
        card = CARDS.get(self.path)
        if card is None:
            return self._send(404)
        self.send_response(200)
        self.send_header("Content-Type", "text/vcard")
        self.send_header("ETag", f'"{self.path}"')
        self.send_header("Content-Length", str(len(card.encode())))
        self.end_headers()
        self.wfile.write(card.encode())

    def do_PUT(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        seen.append(("PUT", self.path))
        CARDS[self.path] = body
        self._send(204)

    def do_DELETE(self):
        seen.append(("DELETE", self.path))
        CARDS.pop(self.path, None)
        self._send(204)

    def log_message(self, *a):
        pass


def serve(tls=None):
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Dav)
    if tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(tls)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd.server_address[1]


k = ec.generate_private_key(ec.SECP256R1())
now = dt.datetime.now(dt.timezone.utc)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
     .serial_number(1).not_valid_before(now - dt.timedelta(hours=1)).not_valid_after(now + dt.timedelta(days=1))
     .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
     .sign(k, hashes.SHA256()))
pem = HERE / "e2e-self.pem"
pem.write_bytes(c.public_bytes(serialization.Encoding.PEM) + k.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))

PLAIN, SELF = serve(), serve(pem)


class Mcp:
    def __init__(self, base, verify=""):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CARDDAV_")}
        env.update(CARDDAV_BASE_URL=base, CARDDAV_USERNAME="u", CARDDAV_PASSWORD="secret-pw",
                   CARDDAV_VERIFY_SSL=verify, CARDDAV_TIMEOUT="10")
        self.p = subprocess.Popen(["uv", "run", "--quiet", "--script", SERVER], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                                  text=True, encoding="utf-8")
        self.n = 0
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "t", "version": "1"}})
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")

    def rpc(self, method, params):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def call(self, tool, **args):
        r = self.rpc("tools/call", {"name": tool, "arguments": args})["result"]
        return r.get("isError", False), "\n".join(x.get("text", "") for x in r["content"])

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=30)


failures = 0


def check(label, cond, detail=""):
    global failures
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"\n       -> {detail}"))
    failures += not cond


m = Mcp(f"http://127.0.0.1:{PLAIN}/carddav/")
err, text = m.call("delete_contact", identifier="Max Mustermann")
check("Claude sees the candidate list", err and "fits 2 contacts" in text and "UID-1" in text
      and "UID-2" in text and "ask the user" in text, text)
check("... and nothing was deleted", not [s for s in seen if s[0] == "DELETE"], seen)
err, text = m.call("delete_contact", identifier="UID-2")
check("delete by the uid Claude got works", not err and '"deleted": true' in text.replace(" ", " "), text)
check("... exactly that card", [s for s in seen if s[0] == "DELETE"] == [("DELETE", "/carddav/u/a/2.vcf")], seen)
err, text = m.call("delete_contact", identifier="  ")
check("Claude sees why an empty identifier fails", err and "No contact given" in text, text)

# --- an expired DSM session must not overwrite the contact
err, text = m.call("update_contact", identifier="UID-1", note="Termin Dienstag")
check("normal update works", not err and "Termin Dienstag" in text, text)
STATE["session_over"] = True
before = CARDS["/carddav/u/a/1.vcf"]
seen.clear()
err, text = m.call("update_contact", identifier="UID-1", note="zweiter Versuch")
check("session gone: Claude is told, not told it worked",
      err and ("session" in text.lower() or "did not return a contact" in text), text)
check("... and the card on the NAS is untouched",
      CARDS["/carddav/u/a/1.vcf"] == before and not [s for s in seen if s[0] == "PUT"], seen)
STATE.clear()
m.close()

m = Mcp(f"https://127.0.0.1:{SELF}/carddav/")
err, text = m.call("list_addressbooks")
check("Claude sees the certificate explanation", err and "was not accepted" in text
      and "Zertifikat prüfen" in text, text)
check("... without the password", "secret-pw" not in text, text)
m.close()

print("\nFAILED" if failures else "\nall passed", failures)
sys.exit(1 if failures else 0)
