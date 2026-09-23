"""Certificate checking on by default - both extensions, against local HTTPS servers.

    uv run --with "mcp>=2.0,<3" --with httpx --with cryptography python test_tls.py

Servers (127.0.0.1 only):
  A  self-signed leaf                         (DSM out of the box, simplified)
  B  leaf for nas.example.com signed by own CA, chain sent  (DSM default: "Synology Inc. CA")
Every request is answered 404, so "got past TLS" shows up as a 404 error.
"""
import datetime as dt
import http.server
import importlib.util
import json
import os
import ssl
import subprocess
import sys
import threading
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import ipaddress

HERE = Path(__file__).parent / "tls"
HERE.mkdir(exist_ok=True)
HOME = Path(__file__).resolve().parents[1]
SIBLING = Path(__file__).resolve().parents[4]
CAL = Path(os.environ.get("CAL_DIR", HOME if (HOME / "index.js").is_file()
                          else SIBLING / "synology-calendar-mcp" / "apps" / "server"))
CON = Path(os.environ.get("CON_PY", HOME / "server.py" if (HOME / "server.py").is_file()
                          else SIBLING / "synology-contacts-mcp" / "apps" / "server" / "server.py"))
now = dt.datetime.now(dt.timezone.utc)


def key():
    return ec.generate_private_key(ec.SECP256R1())


def cert(subject_cn, san, issuer_cn, pub, signer, ca=False):
    b = (x509.CertificateBuilder()
         .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_cn)]))
         .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_cn)]))
         .public_key(pub).serial_number(x509.random_serial_number())
         .not_valid_before(now - dt.timedelta(hours=1)).not_valid_after(now + dt.timedelta(days=1))
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if san:
        b = b.add_extension(x509.SubjectAlternativeName(san), critical=False)
    return b.sign(signer, hashes.SHA256())


def pem(obj):
    if isinstance(obj, x509.Certificate):
        return obj.public_bytes(serialization.Encoding.PEM)
    return obj.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


k_self = key()
c_self = cert("127.0.0.1", [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))], "127.0.0.1",
              k_self.public_key(), k_self)
k_ca, k_leaf = key(), key()
c_ca = cert("Test NAS CA", None, "Test NAS CA", k_ca.public_key(), k_ca, ca=True)
c_leaf = cert("nas.example.com", [x509.DNSName("nas.example.com")], "Test NAS CA",
              k_leaf.public_key(), k_ca)
(HERE / "self.pem").write_bytes(pem(c_self) + pem(k_self))
(HERE / "chain.pem").write_bytes(pem(c_leaf) + pem(c_ca) + pem(k_leaf))
(HERE / "ca.pem").write_bytes(pem(c_ca))


class H(http.server.BaseHTTPRequestHandler):
    def _404(self):
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()
    do_GET = do_PROPFIND = do_OPTIONS = do_REPORT = do_PUT = do_DELETE = _404

    def log_message(self, *a):
        pass


def serve(pemfile):
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(pemfile)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd.server_address[1]


# a rejected handshake makes the server thread print a traceback - keep stderr readable
threading.excepthook = lambda args: None
import socketserver  # noqa: E402
socketserver.BaseServer.handle_error = lambda self, request, client_address: None

A, B = serve(HERE / "self.pem"), serve(HERE / "chain.pem")
failures = 0


def check(name, cond, detail=""):
    global failures
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"\n       -> {detail}"))
    failures += not cond


CERT_TEXT = "was not accepted"

# ---------------------------------------------------------------- contacts
os.environ.update({"CARDDAV_USERNAME": "u", "CARDDAV_PASSWORD": "secret-pw"})
spec = importlib.util.spec_from_file_location("server", CON)
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)


def contacts(base, verify):
    srv.BASE_URL, srv.VERIFY_SSL, srv._client = base, verify, None
    try:
        srv.list_addressbooks()
    except srv.CardDavError as e:
        return str(e)
    return "no error"


check("contacts: VERIFY_SSL defaults to on when unset", srv._flag("CARDDAV_VERIFY_SSL_UNSET") is True)
m = contacts(f"https://127.0.0.1:{A}/carddav/", True)
check("contacts: self-signed rejected with explanation",
      CERT_TEXT in m and "self-signed" in m and "Zertifikat prüfen" in m, m)
check("contacts: ... password not in the message", "secret-pw" not in m, m)
m = contacts(f"https://127.0.0.1:{B}/carddav/", True)
check("contacts: foreign CA rejected with explanation", CERT_TEXT in m, m)
m = contacts(f"https://127.0.0.1:{A}/carddav/", False)
check("contacts: switch off -> gets past TLS (404)", CERT_TEXT not in m and "404" in m, m)

# ---------------------------------------------------------------- calendar
manifest = json.loads((CAL / "manifest.json").read_text(encoding="utf-8"))
check("calendar manifest: verify_ssl default true", manifest["user_config"]["verify_ssl"]["default"] is True)
cm = json.loads(CON.with_name("manifest.json").read_text(encoding="utf-8"))
check("contacts manifest: verify_ssl default true", cm["user_config"]["verify_ssl"]["default"] is True)


def calendar(host, verify=None, extra_ca=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CALDAV_", "NODE_"))}
    env.update({"CALDAV_HOST": host, "CALDAV_USERNAME": "u", "CALDAV_PASSWORD": "secret-pw",
                "CALDAV_TIMEOUT": "10"})
    if verify is not None:
        env["CALDAV_VERIFY_SSL"] = verify
    if extra_ca:
        env["NODE_EXTRA_CA_CERTS"] = str(extra_ca)
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "list-calendars", "arguments": {}}},
    ]
    p = subprocess.Popen(["node", str(CAL / "index.js")], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                         text=True, encoding="utf-8")
    p.stdin.write(json.dumps(msgs[0]) + "\n")
    p.stdin.flush()
    first = json.loads(p.stdout.readline())
    for msg in msgs[1:]:
        p.stdin.write(json.dumps(msg) + "\n")
    p.stdin.flush()
    reply = json.loads(p.stdout.readline())
    p.stdin.close()
    try:
        err = p.communicate(timeout=20)[1]
    except subprocess.TimeoutExpired:
        p.kill()
        err = p.communicate()[1]
    text = json.dumps(reply.get("result") or reply.get("error"), ensure_ascii=False)
    return first, text, err


first, t, err = calendar(f"127.0.0.1:{A}")
check("calendar: handshake still immediate", first.get("result", {}).get("serverInfo"), first)
check("calendar: self-signed rejected with explanation (default on)",
      CERT_TEXT in t and "self-signed" in t and "Zertifikat pr" in t and "use-system-ca" not in t, t)
check("calendar: ... password not in reply or stderr", "secret-pw" not in t + err, (t, err))
_, t, _ = calendar(f"127.0.0.1:{B}")
check("calendar: foreign CA rejected with explanation", CERT_TEXT in t, t)
_, t, _ = calendar(f"127.0.0.1:{B}", extra_ca=HERE / "ca.pem")
check("calendar: trusted CA but wrong name rejected with explanation",
      CERT_TEXT in t and "another name" in t and "use-system-ca" not in t, t)
_, t, err = calendar(f"127.0.0.1:{A}", verify="false")
check("calendar: switch off -> gets past TLS (404)", CERT_TEXT not in t and "404" in t, t)
_, t, _ = calendar(f"127.0.0.1:{A}", verify="")
check("calendar: blank switch counts as on", CERT_TEXT in t, t)

print("\nFAILED" if failures else "\nall passed", failures)
sys.exit(1 if failures else 0)

