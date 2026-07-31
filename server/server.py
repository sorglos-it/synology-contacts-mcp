# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=2.0,<3", "httpx>=0.27"]
# ///
"""
CardDAV MCP server, targeted at Synology Contacts (but generic CardDAV).

Exposes address books and contacts as MCP tools. Deliberately never returns
embedded PHOTO / X-APPLE-OL-NOTE blobs: a single Synology vCard can carry a
30 KB base64 JPEG, which would swamp a model's context for zero benefit.

Config via environment:
  CARDDAV_HOST        host name only, e.g. nas.example.com (":port" optional)
  CARDDAV_HTTPS       "true"/"false" (default true) - picks scheme and default port
  CARDDAV_USERNAME
  CARDDAV_PASSWORD
  CARDDAV_VERIFY_SSL  "true"/"false" (default true; Synology self-signed -> false)
  CARDDAV_TIMEOUT     seconds allowed per HTTP request (default 45)

  CARDDAV_BASE_URL    legacy: a complete endpoint URL. Still honoured, and wins
                      over CARDDAV_HOST when both are set.
"""

from __future__ import annotations

import os
import re
import time
import uuid
import datetime as dt
import xml.etree.ElementTree as ET
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse, unquote

import httpx
from pydantic import BaseModel
from mcp.server import MCPServer

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def _flag(name: str, default: bool = True) -> bool:
    """Read a boolean env var. Blank counts as unset: Claude Desktop injects an
    empty string for a user_config switch it has no value for, and silently
    reading that as "off" would flip the protocol behind the user's back."""
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default
    return v not in ("0", "false", "no", "off")


def _seconds(name: str, default: float) -> float:
    """Read a timeout in seconds. Blank, unparsable or non-positive falls back to
    the default rather than raising: a typo in this field must not be the reason
    the whole server refuses to start."""
    v = os.environ.get(name, "").strip()
    if not v:
        return default
    try:
        secs = float(v)
    except ValueError:
        return default
    return secs if secs > 0 else default


BASE_URL = os.environ.get("CARDDAV_BASE_URL", "").strip()
HOST = os.environ.get("CARDDAV_HOST", "").strip()
USE_HTTPS = _flag("CARDDAV_HTTPS")
USERNAME = os.environ.get("CARDDAV_USERNAME", "")
PASSWORD = os.environ.get("CARDDAV_PASSWORD", "")
VERIFY_SSL = _flag("CARDDAV_VERIFY_SSL")

# DSM answers the first authenticated request of a session in five seconds or
# more - measured in clean five-second steps, the signature of a lookup running
# into its own timeout - and serves later ones from its session cache in
# milliseconds. That first request has to fit, so the default is generous.
# The "Zeitlimit pro Anfrage" field of the extension settings feeds this;
# anything launching the server directly sets CARDDAV_TIMEOUT itself.
TIMEOUT = _seconds("CARDDAV_TIMEOUT", 45.0)

# The Synology CardDAV endpoint. DSM serves it off the DSM port, not off 8008.
DAV_PATH = "/carddav/"
DEFAULT_PORT = {True: 5001, False: 5000}  # DSM https / http

DAV = "DAV:"
CARD = "urn:ietf:params:xml:ns:carddav"
NS = {"d": DAV, "c": CARD}

# Properties we never surface: huge binary/opaque blobs.
NOISE_PROPS = {"PHOTO", "LOGO", "SOUND", "KEY", "X-APPLE-OL-NOTE",
               "X-IMAGEHASH", "X-APPLE-OL-MAPPING-INFO"}

CACHE_TTL = 60.0

mcp = MCPServer("carddav-synology")


# --------------------------------------------------------------------------
# vCard primitives
# --------------------------------------------------------------------------

def unfold(raw: str) -> str:
    """RFC 6350 line unfolding."""
    return re.sub(r"\n[ \t]", "", raw.replace("\r\n", "\n").replace("\r", "\n"))


def _split_unquoted(s: str, sep: str) -> list[str]:
    parts, cur, in_q = [], [], False
    for ch in s:
        if ch == '"':
            in_q = not in_q
            cur.append(ch)
        elif ch == sep and not in_q:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def split_escaped(v: str, sep: str = ";") -> list[str]:
    """Split on separators that are not backslash-escaped."""
    parts, cur, i = [], [], 0
    while i < len(v):
        c = v[i]
        if c == "\\" and i + 1 < len(v):
            cur.append(v[i:i + 2])
            i += 2
        elif c == sep:
            parts.append("".join(cur))
            cur = []
            i += 1
        else:
            cur.append(c)
            i += 1
    parts.append("".join(cur))
    return parts


def unescape(v: str) -> str:
    out, i = [], 0
    mapping = {"n": "\n", "N": "\n", "\\": "\\", ",": ",", ";": ";"}
    while i < len(v):
        c = v[i]
        if c == "\\" and i + 1 < len(v):
            out.append(mapping.get(v[i + 1], v[i + 1]))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def escape(v: str) -> str:
    return (v.replace("\\", "\\\\").replace(";", "\\;")
             .replace(",", "\\,").replace("\n", "\\n"))


def fold(line: str) -> str:
    """Fold to 75 octets without splitting a multi-byte UTF-8 sequence."""
    b = line.encode("utf-8")
    if len(b) <= 75:
        return line
    chunks, i, limit = [], 0, 75
    while i < len(b):
        end = min(i + limit, len(b))
        if end < len(b):
            while end > i and (b[end] & 0xC0) == 0x80:
                end -= 1
        chunks.append(b[i:end].decode("utf-8"))
        i, limit = end, 74
    return "\r\n ".join(chunks)


class Prop:
    __slots__ = ("group", "name", "params", "value", "raw")

    def __init__(self, group, name, params, value, raw):
        self.group = group
        self.name = name
        self.params = params
        self.value = value
        self.raw = raw

    def types(self) -> list[str]:
        out = []
        for t in self.params.get("TYPE", []):
            t = t.strip('"')
            # Apple writes _$!<Other>!$_
            m = re.fullmatch(r"_\$!<(.+)>!\$_", t)
            if m:
                t = m.group(1)
            if t and t.lower() != "pref":
                out.append(t.lower())
        return out

    def is_pref(self) -> bool:
        return any(t.strip('"').lower() == "pref"
                   for t in self.params.get("TYPE", [])) or \
               self.params.get("PREF", []) != []


def parse_prop(line: str) -> Prop | None:
    idx, in_q = -1, False
    for i, ch in enumerate(line):
        if ch == '"':
            in_q = not in_q
        elif ch == ":" and not in_q:
            idx = i
            break
    if idx < 0:
        return None
    head, value = line[:idx], line[idx + 1:]
    segs = _split_unquoted(head, ";")
    name = segs[0]
    group = None
    if "." in name:
        group, name = name.split(".", 1)
    params: dict[str, list[str]] = {}
    for p in segs[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
        else:
            k, v = "TYPE", p
        vals = [x.strip().strip('"') for x in _split_unquoted(v, ",")]
        params.setdefault(k.strip().upper(), []).extend([x for x in vals if x])
    return Prop(group, name.upper(), params, value, line)


def parse_vcard(raw: str) -> list[Prop]:
    props = []
    for line in unfold(raw).split("\n"):
        line = line.rstrip()
        if not line:
            continue
        p = parse_prop(line)
        if p:
            props.append(p)
    return props


def summarize(props: list[Prop], href: str = "", etag: str = "",
              full: bool = False) -> dict[str, Any]:
    """Turn parsed props into a compact, model-friendly dict."""
    labels = {p.group: unescape(p.value) for p in props
              if p.name == "X-ABLABEL" and p.group}

    def label_for(p: Prop, default: str = "") -> str:
        if p.group and p.group in labels:
            lb = labels[p.group]
            m = re.fullmatch(r"_\$!<(.+)>!\$_", lb)
            if m:
                lb = m.group(1)
            return lb
        t = p.types()
        return ", ".join(t) if t else default

    out: dict[str, Any] = {}
    emails, phones, addresses, urls = [], [], [], []
    has_photo = False

    for p in props:
        n = p.name
        if n in ("BEGIN", "END", "VERSION", "PRODID"):
            continue
        if n in NOISE_PROPS:
            if n == "PHOTO":
                has_photo = True
            continue
        if n == "FN":
            out["full_name"] = unescape(p.value)
        elif n == "N":
            c = [unescape(x) for x in split_escaped(p.value)]
            c += [""] * (5 - len(c))
            out["last_name"], out["first_name"] = c[0], c[1]
            if c[3]:
                out["prefix"] = c[3]
            if c[4]:
                out["suffix"] = c[4]
        elif n == "ORG":
            parts = [unescape(x) for x in split_escaped(p.value) if x]
            if parts:
                out["organization"] = parts[0]
                if len(parts) > 1:
                    out["department"] = " / ".join(parts[1:])
        elif n == "TITLE":
            out["job_title"] = unescape(p.value)
        elif n == "EMAIL":
            emails.append({"value": unescape(p.value),
                           "type": label_for(p, "other"),
                           **({"preferred": True} if p.is_pref() else {})})
        elif n == "TEL":
            phones.append({"value": unescape(p.value),
                           "type": label_for(p, "other"),
                           **({"preferred": True} if p.is_pref() else {})})
        elif n == "ADR":
            c = [unescape(x) for x in split_escaped(p.value)]
            c += [""] * (7 - len(c))
            addresses.append({
                "type": label_for(p, "other"),
                "street": c[2], "city": c[3], "region": c[4],
                "postal_code": c[5], "country": c[6],
            })
        elif n == "URL":
            urls.append({"value": unescape(p.value), "type": label_for(p, "")})
        elif n == "NOTE":
            out["note"] = unescape(p.value)
        elif n == "BDAY":
            out["birthday"] = unescape(p.value)
        elif n == "NICKNAME":
            out["nickname"] = unescape(p.value)
        elif n == "CATEGORIES":
            out["categories"] = [unescape(x) for x in split_escaped(p.value, ",")]
        elif n == "UID":
            out["uid"] = unescape(p.value)
        elif n == "REV" and full:
            out["last_modified"] = unescape(p.value)

    if emails:
        out["emails"] = emails
    if phones:
        out["phones"] = phones
    if addresses:
        out["addresses"] = addresses
    if urls:
        out["urls"] = urls
    if has_photo:
        out["has_photo"] = True
    if href:
        out["href"] = href
    if etag and full:
        out["etag"] = etag
    if "full_name" not in out:
        nm = " ".join(x for x in (out.get("first_name"), out.get("last_name")) if x)
        out["full_name"] = nm or out.get("organization", "(unnamed)")
    return out


# --------------------------------------------------------------------------
# CardDAV client
# --------------------------------------------------------------------------

class CardDavError(RuntimeError):
    pass


def compose_base_url(host: str, https: bool) -> str:
    """Build the CardDAV endpoint from a bare host name.

    The settings dialog asks for a host, but people paste whole URLs into any
    field that looks like it wants one. So a pasted scheme, path or query is
    stripped instead of rejected: the protocol switch decides the scheme, the
    path is always DAV_PATH, and only an explicit :port survives. Getting the
    path wrong is what makes DSM answer with its login page.
    """
    h = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", host.strip())
    h = h.split("/", 1)[0].split("?", 1)[0].strip().strip(".")
    if "@" in h:  # user:pass@host
        h = h.rsplit("@", 1)[-1]
    if not h:
        raise CardDavError(
            f"CARDDAV_HOST is empty. Set it to the NAS host name, e.g. "
            f"nas.example.com (port defaults to {DEFAULT_PORT[https]})."
        )
    # rsplit on "]" keeps an IPv6 literal like [::1] from looking like host:port
    if ":" not in h.rsplit("]", 1)[-1]:
        h = f"{h}:{DEFAULT_PORT[https]}"
    return f"{'https' if https else 'http'}://{h}{DAV_PATH}"


class Client:
    def __init__(self):
        if BASE_URL:
            self.base = BASE_URL if BASE_URL.endswith("/") else BASE_URL + "/"
            # A bare host is the classic misconfiguration of the legacy URL
            # field: DSM then answers the PROPFIND with its login page, and the
            # HTML entities in it produce a baffling "undefined entity" parse
            # error. Point at the DAV root instead.
            if urlparse(self.base).path == "/":
                self.base += DAV_PATH.lstrip("/")
        elif HOST:
            self.base = compose_base_url(HOST, USE_HTTPS)
        else:
            raise CardDavError(
                "No server configured. Set CARDDAV_HOST to the NAS host name "
                "(e.g. nas.example.com); in Claude Desktop that is the "
                "\"NAS-Adresse\" field of the extension settings."
            )
        pr = urlparse(self.base)
        self.origin = f"{pr.scheme}://{pr.netloc}"
        self._http = httpx.Client(
            auth=httpx.BasicAuth(USERNAME, PASSWORD),
            verify=VERIFY_SSL,
            timeout=httpx.Timeout(TIMEOUT),
            headers={"User-Agent": "carddav-mcp/1.0"},
            follow_redirects=True,
        )
        self._books: list[dict[str, str]] | None = None
        self._cache: dict[str, tuple[float, list[dict]]] = {}

    def abs(self, href: str) -> str:
        if href.startswith("http://") or href.startswith("https://"):
            return href
        return self.origin + href if href.startswith("/") else urljoin(self.base, href)

    def _req(self, method: str, url: str, body: str | None = None,
             depth: str | None = None, extra: dict | None = None,
             content_type: str = "application/xml; charset=utf-8") -> httpx.Response:
        headers = {}
        if depth is not None:
            headers["Depth"] = depth
        if body is not None:
            headers["Content-Type"] = content_type
        if extra:
            headers.update(extra)
        try:
            r = self._http.request(method, url, content=body.encode("utf-8") if body else None,
                                   headers=headers)
        except httpx.HTTPError as e:
            raise CardDavError(f"{method} {url} failed: {e}") from e
        if r.status_code == 401:
            raise CardDavError(
                f"Authentication failed (401) for {USERNAME}. "
                "Check CARDDAV_USERNAME / CARDDAV_PASSWORD."
            )
        if r.status_code == 403:
            raise CardDavError(
                f"Permission denied (403) for {method} on {url}. "
                "This address book is most likely read-only for this account "
                "(shared books often are) - use a writable one."
            )
        if r.status_code == 412:
            raise CardDavError(
                "Precondition failed (412): the contact changed on the server "
                "since it was read, or it already exists. Re-read and retry."
            )
        if r.status_code >= 400:
            raise CardDavError(f"{method} {url} -> HTTP {r.status_code}: {r.text[:300]}")
        return r

    def _xml(self, r: httpx.Response) -> ET.Element:
        """Parse a DAV response, naming the real problem when it is not XML.

        A wrong base URL makes DSM answer with its login page, whose &nbsp; then
        surfaces as "undefined entity: line 7, column 0" - useless on its own.
        """
        try:
            return ET.fromstring(r.content)
        except ET.ParseError as e:
            ctype = r.headers.get("Content-Type", "unknown")
            head = r.content.lstrip()[:9].lower()
            if "html" in ctype.lower() or head == b"<!doctype" or head[:5] == b"<html":
                raise CardDavError(
                    f"{r.request.url} answered with a web page ({ctype}) instead of "
                    "CardDAV. CARDDAV_BASE_URL is most likely missing the DAV path - "
                    "it has to be the CardDAV endpoint itself, e.g. "
                    "https://nas.example.com:5001/carddav/"
                ) from e
            raise CardDavError(
                f"{r.request.url} returned XML that cannot be parsed ({ctype}): {e}"
            ) from e

    # -- discovery ---------------------------------------------------------

    def _propfind_prop(self, url: str, prop_xml: str, depth: str = "0") -> ET.Element:
        body = ('<?xml version="1.0" encoding="utf-8"?>'
                f'<d:propfind xmlns:d="DAV:" xmlns:c="{CARD}"><d:prop>{prop_xml}'
                "</d:prop></d:propfind>")
        r = self._req("PROPFIND", url, body, depth=depth)
        return self._xml(r)

    def addressbooks(self) -> list[dict[str, str]]:
        if self._books is not None:
            return self._books

        root = self._propfind_prop(self.base, "<d:current-user-principal/>")
        principal = None
        for href in root.iter(f"{{{DAV}}}current-user-principal"):
            for h in href.iter(f"{{{DAV}}}href"):
                principal = h.text
        home = None
        if principal:
            purl = self.abs(principal)
            root = self._propfind_prop(purl, "<c:addressbook-home-set/>")
            for hs in root.iter(f"{{{CARD}}}addressbook-home-set"):
                for h in hs.iter(f"{{{DAV}}}href"):
                    home = h.text
            home = self.abs(home) if home else purl
        else:
            home = self.base

        root = self._propfind_prop(
            home,
            "<d:resourcetype/><d:displayname/><d:current-user-privilege-set/>",
            depth="1",
        )
        books = []
        for resp in root.iter(f"{{{DAV}}}response"):
            href_el = resp.find(f"{{{DAV}}}href")
            if href_el is None or not href_el.text:
                continue
            is_ab = resp.find(f".//{{{CARD}}}addressbook") is not None
            if not is_ab:
                continue
            dn_el = resp.find(f".//{{{DAV}}}displayname")
            name = (dn_el.text or "").strip() if dn_el is not None else ""
            href = href_el.text
            if not name:
                name = unquote(href.rstrip("/").rsplit("/", 1)[-1])
            privs = resp.find(f".//{{{DAV}}}current-user-privilege-set")
            if privs is None:
                writable = None  # server did not report privileges
            else:
                tags = {str(e.tag).rsplit("}", 1)[-1] for e in privs.iter()}
                writable = bool(tags & {"write", "write-content", "all", "bind"})
            books.append({"name": name, "href": href, "url": self.abs(href),
                          "writable": writable})
        if not books:
            raise CardDavError(f"No address books found under {home}")
        self._books = books
        return books

    def resolve_book(self, ref: str | None) -> dict[str, str]:
        books = self.addressbooks()
        if not ref:
            return books[0]
        low = ref.strip().lower()
        for b in books:
            if b["name"].lower() == low or b["href"] == ref or b["url"] == ref:
                return b
        for b in books:
            if low in b["name"].lower() or low in b["href"].lower():
                return b
        raise CardDavError(
            f"Address book {ref!r} not found. Available: "
            + ", ".join(repr(b["name"]) for b in books)
        )

    # -- reading -----------------------------------------------------------

    def fetch_all(self, book: dict[str, str], use_cache: bool = True) -> list[dict]:
        key = book["url"]
        now = time.time()
        if use_cache and key in self._cache:
            ts, data = self._cache[key]
            if now - ts < CACHE_TTL:
                return data
        body = ('<?xml version="1.0" encoding="utf-8"?>'
                f'<c:addressbook-query xmlns:d="DAV:" xmlns:c="{CARD}">'
                "<d:prop><d:getetag/><c:address-data/></d:prop>"
                '<c:filter><c:prop-filter name="FN"/></c:filter>'
                "</c:addressbook-query>")
        r = self._req("REPORT", book["url"], body, depth="1")
        root = self._xml(r)
        items = []
        for resp in root.iter(f"{{{DAV}}}response"):
            href_el = resp.find(f"{{{DAV}}}href")
            data_el = resp.find(f".//{{{CARD}}}address-data")
            etag_el = resp.find(f".//{{{DAV}}}getetag")
            if href_el is None or data_el is None or not (data_el.text or "").strip():
                continue
            items.append({
                "href": href_el.text,
                "etag": (etag_el.text or "") if etag_el is not None else "",
                "raw": data_el.text,
            })
        self._cache[key] = (now, items)
        return items

    def get_raw(self, href: str) -> tuple[str, str]:
        r = self._req("GET", self.abs(href))
        return r.text, r.headers.get("ETag", "")

    def put(self, url: str, vcard: str, etag: str | None = None,
            create: bool = False) -> str:
        extra = {}
        if create:
            extra["If-None-Match"] = "*"
        elif etag:
            extra["If-Match"] = etag
        r = self._req("PUT", url, vcard, extra=extra, content_type="text/vcard; charset=utf-8")
        self._cache.clear()
        return r.headers.get("ETag", "")

    def delete(self, url: str, etag: str | None = None) -> None:
        self._req("DELETE", url, extra={"If-Match": etag} if etag else None)
        self._cache.clear()

    def find(self, ident: str, book_ref: str | None = None) -> tuple[dict, dict]:
        """Locate one contact by UID, href, or exact//partial display name."""
        books = [self.resolve_book(book_ref)] if book_ref else self.addressbooks()
        ident_l = ident.strip().lower()
        partial = []
        for b in books:
            for it in self.fetch_all(b):
                props = parse_vcard(it["raw"])
                s = summarize(props, it["href"], it["etag"], full=True)
                if (s.get("uid", "").lower() == ident_l
                        or it["href"] == ident
                        or it["href"].rsplit("/", 1)[-1].lower() == ident_l
                        or s.get("full_name", "").lower() == ident_l):
                    return s, it
                if ident_l and ident_l in s.get("full_name", "").lower():
                    partial.append((s, it))
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            names = ", ".join(repr(p[0].get("full_name")) for p in partial[:8])
            raise CardDavError(f"{ident!r} is ambiguous, matches: {names}. Use the uid.")
        raise CardDavError(f"No contact matching {ident!r}.")


_client: Client | None = None


def client() -> Client:
    global _client
    if _client is None:
        _client = Client()
    return _client


# --------------------------------------------------------------------------
# vCard building
# --------------------------------------------------------------------------

class ContactField(BaseModel):
    value: str
    type: str = "home"


def _prop_line(name: str, value: str, types: Iterable[str] = ()) -> str:
    tp = "".join(f";TYPE={t}" for t in types if t)
    return fold(f"{name}{tp}:{value}")


def build_vcard(uid: str, *, full_name: str | None = None,
                first_name: str | None = None, last_name: str | None = None,
                organization: str | None = None, job_title: str | None = None,
                emails: list[ContactField] | None = None,
                phones: list[ContactField] | None = None,
                note: str | None = None, birthday: str | None = None,
                url: str | None = None,
                categories: list[str] | None = None) -> str:
    fn = full_name or " ".join(x for x in (first_name, last_name) if x) or organization or "Unnamed"
    lines = ["BEGIN:VCARD", "VERSION:3.0", "PRODID:-//carddav-mcp//EN", f"UID:{uid}"]
    lines.append(_prop_line("FN", escape(fn)))
    lines.append(_prop_line("N", f"{escape(last_name or '')};{escape(first_name or '')};;;"))
    if organization:
        lines.append(_prop_line("ORG", escape(organization) + ";"))
    if job_title:
        lines.append(_prop_line("TITLE", escape(job_title)))
    for e in emails or []:
        lines.append(_prop_line("EMAIL", escape(e.value), ["INTERNET", e.type.upper()]))
    for p in phones or []:
        lines.append(_prop_line("TEL", escape(p.value), [p.type.upper()]))
    if url:
        lines.append(_prop_line("URL", escape(url)))
    if birthday:
        lines.append(_prop_line("BDAY", escape(birthday)))
    if note:
        lines.append(_prop_line("NOTE", escape(note)))
    if categories:
        lines.append(_prop_line("CATEGORIES", ",".join(escape(c) for c in categories)))
    lines.append(f"REV:{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


def patch_vcard(raw: str, changes: dict[str, Any]) -> str:
    """Rewrite only the given properties, preserving PHOTO / X-* / groups."""
    props = parse_vcard(raw)
    drop: set[str] = set()
    new_lines: list[str] = []

    def set_simple(name: str, value: str | None, esc: bool = True):
        if value is None:
            return
        drop.add(name)
        if value != "":
            new_lines.append(_prop_line(name, escape(value) if esc else value))

    fn = changes.get("full_name")
    first, last = changes.get("first_name"), changes.get("last_name")
    if first is not None or last is not None:
        cur = next((p for p in props if p.name == "N"), None)
        c = [unescape(x) for x in split_escaped(cur.value)] if cur else []
        c += [""] * (5 - len(c))
        if last is not None:
            c[0] = last
        if first is not None:
            c[1] = first
        drop.add("N")
        new_lines.append(_prop_line("N", ";".join(escape(x) for x in c[:5])))
        if fn is None:
            fn = " ".join(x for x in (c[1], c[0]) if x)
    set_simple("FN", fn)
    if changes.get("organization") is not None:
        drop.add("ORG")
        if changes["organization"]:
            new_lines.append(_prop_line("ORG", escape(changes["organization"]) + ";"))
    set_simple("TITLE", changes.get("job_title"))
    set_simple("NOTE", changes.get("note"))
    set_simple("BDAY", changes.get("birthday"))
    set_simple("URL", changes.get("url"))
    if changes.get("categories") is not None:
        drop.add("CATEGORIES")
        if changes["categories"]:
            new_lines.append(_prop_line(
                "CATEGORIES", ",".join(escape(c) for c in changes["categories"])))
    if changes.get("emails") is not None:
        drop.add("EMAIL")
        for e in changes["emails"]:
            new_lines.append(_prop_line("EMAIL", escape(e.value), ["INTERNET", e.type.upper()]))
    if changes.get("phones") is not None:
        drop.add("TEL")
        for p in changes["phones"]:
            new_lines.append(_prop_line("TEL", escape(p.value), [p.type.upper()]))

    kept = []
    for p in props:
        if p.name in ("BEGIN", "END", "REV"):
            continue
        if p.name in drop:
            continue
        kept.append(p.raw)
    rev = f"REV:{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    body = ["BEGIN:VCARD"] + kept + new_lines + [rev, "END:VCARD"]
    # VERSION must directly follow BEGIN
    body = [b for b in body if not b.startswith("VERSION:")]
    body.insert(1, "VERSION:3.0")
    return "\r\n".join(fold(b) if len(b.encode()) > 75 and "\n" not in b else b
                       for b in body) + "\r\n"


# --------------------------------------------------------------------------
# MCP tools
# --------------------------------------------------------------------------

@mcp.tool()
def list_addressbooks() -> list[dict]:
    """List the CardDAV address books available on the server."""
    c = client()
    out = []
    for b in c.addressbooks():
        try:
            n = len(c.fetch_all(b))
        except CardDavError:
            n = -1
        row = {"name": b["name"], "contacts": n, "href": b["href"]}
        if b.get("writable") is not None:
            row["writable"] = b["writable"]
        out.append(row)
    return out


@mcp.tool()
def list_contacts(addressbook: str | None = None, limit: int = 100,
                  offset: int = 0) -> dict:
    """List contacts (photos omitted).

    addressbook: name or href; defaults to the first address book.
    """
    c = client()
    book = c.resolve_book(addressbook)
    items = c.fetch_all(book)
    rows = [summarize(parse_vcard(i["raw"]), i["href"], i["etag"]) for i in items]
    rows.sort(key=lambda r: r.get("full_name", "").lower())
    window = rows[offset:offset + max(1, min(limit, 500))]
    return {"addressbook": book["name"], "total": len(rows),
            "returned": len(window), "offset": offset, "contacts": window}


@mcp.tool()
def search_contacts(query: str, addressbook: str | None = None,
                    limit: int = 25) -> dict:
    """Search contacts by name, organisation, email, phone or note."""
    c = client()
    books = [c.resolve_book(addressbook)] if addressbook else c.addressbooks()
    q = query.strip().lower()
    digits = re.sub(r"\D", "", q)
    hits = []
    for b in books:
        for i in c.fetch_all(b):
            s = summarize(parse_vcard(i["raw"]), i["href"], i["etag"])
            hay = " ".join([
                s.get("full_name", ""), s.get("organization", ""),
                s.get("job_title", ""), s.get("note", ""),
                " ".join(e["value"] for e in s.get("emails", [])),
                " ".join(p["value"] for p in s.get("phones", [])),
            ]).lower()
            match = q in hay
            if not match and len(digits) >= 4:
                tel = re.sub(r"\D", "", " ".join(p["value"] for p in s.get("phones", [])))
                match = digits in tel
            if match:
                s["addressbook"] = b["name"]
                hits.append(s)
    hits.sort(key=lambda r: (not r.get("full_name", "").lower().startswith(q),
                             r.get("full_name", "").lower()))
    return {"query": query, "matches": len(hits), "contacts": hits[:max(1, min(limit, 200))]}


@mcp.tool()
def get_contact(identifier: str, addressbook: str | None = None) -> dict:
    """Full detail for one contact, found by UID, href or display name."""
    s, _ = client().find(identifier, addressbook)
    return s


@mcp.tool()
def create_contact(full_name: str | None = None, first_name: str | None = None,
                   last_name: str | None = None, organization: str | None = None,
                   job_title: str | None = None,
                   emails: list[ContactField] | None = None,
                   phones: list[ContactField] | None = None,
                   note: str | None = None, birthday: str | None = None,
                   url: str | None = None, categories: list[str] | None = None,
                   addressbook: str | None = None) -> dict:
    """Create a contact. birthday is YYYY-MM-DD. Field type e.g. home/work/cell."""
    if not any([full_name, first_name, last_name, organization]):
        raise CardDavError("Provide at least full_name, first/last_name or organization.")
    c = client()
    book = c.resolve_book(addressbook)
    uid = str(uuid.uuid4()).upper()
    vcard = build_vcard(uid, full_name=full_name, first_name=first_name,
                        last_name=last_name, organization=organization,
                        job_title=job_title, emails=emails, phones=phones,
                        note=note, birthday=birthday, url=url, categories=categories)
    target = book["url"].rstrip("/") + f"/{uid}.vcf"
    c.put(target, vcard, create=True)
    return {"created": True, "uid": uid, "addressbook": book["name"],
            "href": urlparse(target).path,
            "full_name": full_name or " ".join(x for x in (first_name, last_name) if x)
                         or organization}


@mcp.tool()
def update_contact(identifier: str, full_name: str | None = None,
                   first_name: str | None = None, last_name: str | None = None,
                   organization: str | None = None, job_title: str | None = None,
                   emails: list[ContactField] | None = None,
                   phones: list[ContactField] | None = None,
                   note: str | None = None, birthday: str | None = None,
                   url: str | None = None, categories: list[str] | None = None,
                   addressbook: str | None = None) -> dict:
    """Update a contact. Only supplied fields change; photo and other data are kept.

    Passing emails/phones replaces the whole list for that kind.
    """
    c = client()
    summary, item = c.find(identifier, addressbook)
    raw, etag = c.get_raw(item["href"])
    changes = {"full_name": full_name, "first_name": first_name,
               "last_name": last_name, "organization": organization,
               "job_title": job_title, "emails": emails, "phones": phones,
               "note": note, "birthday": birthday, "url": url,
               "categories": categories}
    if all(v is None for v in changes.values()):
        raise CardDavError("No fields given to update.")
    new = patch_vcard(raw, changes)
    c.put(c.abs(item["href"]), new, etag=etag or item.get("etag") or None)
    s2, _ = c.find(summary.get("uid") or identifier, addressbook)
    return {"updated": True, "contact": s2}


@mcp.tool()
def delete_contact(identifier: str, addressbook: str | None = None) -> dict:
    """Permanently delete a contact. Identify precisely (UID preferred)."""
    c = client()
    summary, item = c.find(identifier, addressbook)
    c.delete(c.abs(item["href"]), etag=item.get("etag") or None)
    return {"deleted": True, "uid": summary.get("uid"),
            "full_name": summary.get("full_name")}


if __name__ == "__main__":
    mcp.run()
