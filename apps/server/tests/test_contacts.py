"""Unit tests for synology-contacts server.py against an in-memory CardDAV store.

    uv run --with "mcp>=2.0,<3" --with httpx python test_contacts_find.py
"""
import hashlib
import importlib.util
import os
import sys
from pathlib import Path
from xml.sax.saxutils import escape

os.environ.update({"CARDDAV_HOST": "nas.test", "CARDDAV_USERNAME": "u", "CARDDAV_PASSWORD": "p"})
APP = Path(__file__).resolve().parents[1]
SERVER = os.environ.get("SERVER_PY", str(APP / "server.py"))
spec = importlib.util.spec_from_file_location("server", SERVER)
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)
import httpx  # noqa: E402

ORIGIN = "https://nas.test:5001"
BOOKS = [("/carddav/u/a/", "Kontakte"), ("/carddav/u/b/", "Kontakte"), ("/carddav/u/c/", "Firma")]
PHOTO = "PHOTO;ENCODING=b;TYPE=JPEG:" + "A" * 30000


def card(uid, fn, *extra, version="3.0"):
    lines = ["BEGIN:VCARD", f"VERSION:{version}"] + ([f"UID:{uid}"] if uid else [])
    lines += [f"FN:{fn}", "N:;;;;", *extra, "END:VCARD"]
    return "\r\n".join(lines) + "\r\n"


def etag(v):
    return '"' + hashlib.md5(v.encode()).hexdigest() + '"'


store, calls, bodies, EXTRA = {}, [], {}, {}


def reset():
    store.clear()
    store.update({
        "/carddav/u/a/1.vcf": card("UID-1", "Max Mustermann", "TEL:0170 111"),
        "/carddav/u/a/2.vcf": card("UID-2", "Max Mustermann", "EMAIL:max@work.test"),
        "/carddav/u/a/3.vcf": card("", "Erika Musterfrau"),
        "/carddav/u/a/7.vcf": card("UID-7", ""),
        "/carddav/u/b/4.vcf": card("UID-4", "Hans Meier"),
        "/carddav/u/b/5.vcf": card("UID-1", "Max Mustermann Kopie"),
        "/carddav/u/c/6.vcf": card("UID-6", "Anna Schmidt"),
    })
    calls.clear()


def handler(request: httpx.Request) -> httpx.Response:
    path, m = request.url.path, request.method
    calls.append((m, path, request.headers.get("If-Match")))
    if EXTRA.get("redirect_read") and m in ("GET", "REPORT", "PROPFIND"):
        # a NAS (or something in between) sending reads somewhere else
        return httpx.Response(302, headers={"Location": "https://evil.test/x.vcf"})
    if m == "REPORT":
        tag = "" if EXTRA.get("etag") == "none" else "<d:getetag>{}</d:getetag>"
        body = EXTRA.get("response", "") + "".join(
            f"<d:response><d:href>{h}</d:href><d:propstat><d:prop>"
            + tag.format(etag(v)) + f"<c:address-data>{escape(v)}</c:address-data>"
            "</d:prop></d:propstat></d:response>"
            for h, v in store.items() if h.startswith(path))
        return httpx.Response(207, content=(
            '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" '
            f'xmlns:c="urn:ietf:params:xml:ns:carddav">{body}</d:multistatus>').encode())
    if m == "PROPFIND":
        # what count() asks for: one response per resource, collection first
        body = f"<d:response><d:href>{path}</d:href></d:response>" + "".join(
            f'<d:response><d:href>{h}</d:href><d:propstat><d:prop>'
            f"<d:getetag>{etag(v)}</d:getetag></d:prop></d:propstat></d:response>"
            for h, v in store.items() if h.startswith(path))
        return httpx.Response(207, content=(
            f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">{body}</d:multistatus>').encode())
    if m in ("PUT", "DELETE") and path.endswith("moved.vcf"):
        return httpx.Response(302, headers={"Location": ORIGIN + "/somewhere/else"})
    if EXTRA.get("session_over") and m in ("GET", "PUT", "DELETE"):
        # DSM with an expired session: its login page, with HTTP 200
        return httpx.Response(200, text="<html>login</html>",
                              headers={"Content-Type": "text/html"})
    if m == "GET":
        if path not in store:
            return httpx.Response(200, text="<html>login</html>",
                                  headers={"Content-Type": "text/html"})
        head = {"ETag": etag(store[path])}
        if EXTRA.get("etag") == "none":
            head = {}
        elif EXTRA.get("etag") == "weak":
            head = {"ETag": "W/" + etag(store[path])}
        card = store[path]
        if EXTRA.get("changes_behind_us"):
            # somebody else writes to the card right after we read it
            store[path] = card.replace("END:VCARD", "TEL:0170 NEU\r\nEND:VCARD")
        return httpx.Response(200, text=card, headers=head)
    if m in ("PUT", "DELETE"):
        im = request.headers.get("If-Match")
        if im and im != "*" and path in store and im != etag(store[path]):
            return httpx.Response(412)
        if im and im.upper().startswith("W/"):  # not allowed in If-Match
            return httpx.Response(400)
    if m == "PUT":
        store[path] = request.content.decode()
        bodies[path] = store[path]
        return httpx.Response(204, headers={"ETag": etag(store[path])})
    if m == "DELETE":
        del store[path]
        return httpx.Response(204)
    return httpx.Response(500)


def fresh_client():
    c = srv.Client()
    c._http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    c._books = [{"name": n, "href": h, "url": ORIGIN + h, "writable": True} for h, n in BOOKS]
    srv._client = c
    return c


failures = 0


def check(name, cond, detail=""):
    global failures
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))
    failures += not cond


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except srv.CardDavError as e:
        return str(e)
    return None


def writes():
    return [c for c in calls if c[0] in ("PUT", "DELETE")]


F = srv.ContactField
reset(); c = fresh_client()

# --- same name more than once: never the first one, nothing written
msg = raises(srv.delete_contact, "Max Mustermann")
check("delete by duplicate name refuses", msg and "fits 3 contacts" in msg, msg)
check("... exact names listed first", msg and msg.split("\n")[3].startswith("- Max Mustermann Kopie"), msg)
check("... lists what tells them apart",
      msg and "0170 111" in msg and "max@work.test" in msg, msg)
check("... tells Claude to ask", msg and "ask the user" in msg, msg)
check("... sends no DELETE", not writes(), writes())
check("... UID-1 repeats, so hrefs are offered for those two",
      msg and "identifier /carddav/u/a/1.vcf" in msg and "identifier /carddav/u/b/5.vcf" in msg
      and "identifier UID-2" in msg, msg)
msg = raises(srv.update_contact, "max mustermann", note="x")
check("update by duplicate name refuses, no PUT", msg and not writes(), (msg, writes()))

# --- the identifiers offered resolve in one step
check("offered href resolves", srv.get_contact("/carddav/u/a/1.vcf")["uid"] == "UID-1")
check("offered uid resolves", srv.get_contact("UID-2").get("emails"))
check("full URL of href resolves",
      srv.get_contact(ORIGIN + "/carddav/u/b/5.vcf")["full_name"] == "Max Mustermann Kopie")

# --- one UID in two books
msg = raises(srv.get_contact, "UID-1")
check("same UID in two books refuses", msg and "fits 2 contacts" in msg, msg)
check("uid scoped to one book resolves",
      srv.get_contact("UID-1", addressbook="/carddav/u/b/")["href"] == "/carddav/u/b/5.vcf")

# --- empty identifiers never match cards without UID or name
for ident in ("", "   "):
    msg = raises(srv.delete_contact, ident)
    check(f"delete({ident!r}) refused", msg and "No contact given" in msg, msg)
check("... nothing deleted", not writes() and len(store) == 7, writes())

# --- a name has to be unique among all names containing it
check("unique name", srv.get_contact("Hans Meier")["uid"] == "UID-4")
check("unique part of a name", srv.get_contact("schmidt")["uid"] == "UID-6")
check("longer name unique", srv.get_contact("Max Mustermann Kopie")["href"] == "/carddav/u/b/5.vcf")
check("unknown name", "No contact matching" in (raises(srv.get_contact, "Nobody") or ""))
store["/carddav/u/c/m1.vcf"] = card("M1", "Müller", "ORG:Müller GmbH;")
store["/carddav/u/c/m2.vcf"] = card("M2", "Anna Müller")
store["/carddav/u/c/m3.vcf"] = card("M3", "Hans Müller")
c._cache.clear(); calls.clear()
msg = raises(srv.delete_contact, "Müller")
check("exact 'Müller' does not outvote Anna and Hans Müller", msg and "fits 3 contacts" in msg
      and msg.split("\n")[1].startswith("- Müller (Müller GmbH)"), msg)
check("... no DELETE", not writes(), writes())

# --- address books with the same name
msg = raises(srv.get_contact, "Hans Meier", addressbook="Kontakte")
check("duplicate book name refuses", msg and "fits 2 address books" in msg, msg)
msg = raises(srv.create_contact, full_name="Neu", addressbook="Kontakte")
check("create into duplicate book name refuses, no PUT", msg and not writes(), (msg, writes()))
check("book by href", srv.list_contacts(addressbook="/carddav/u/b/")["total"] == 2)
check("book by partial unique name", srv.list_contacts(addressbook="fir")["addressbook"] == "Firma")
check("blank book = all books", srv.search_contacts("mustermann", addressbook="  ")["matches"] == 3)
check("no book = first book", srv.list_contacts()["total"] == 4)
check("unknown book", "not found" in (raises(srv.list_contacts, addressbook="xyz") or ""))

# --- update writes the right card and reads back that very card
calls.clear()
r = srv.update_contact("UID-2", note="hallo")
puts = [x for x in calls if x[0] == "PUT"]
check("update PUTs the right card with If-Match",
      len(puts) == 1 and puts[0][1] == "/carddav/u/a/2.vcf" and puts[0][2], puts)
check("update reads the card back by href", calls[-1][:2] == ("GET", "/carddav/u/a/2.vcf"), calls[-1])
check("update result is that card", r["contact"]["uid"] == "UID-2"
      and r["contact"]["note"] == "hallo" and r["contact"]["addressbook"] == "Kontakte", r)

# --- delete by uid removes exactly that card
calls.clear()
r = srv.delete_contact("UID-2")
dels = [x for x in calls if x[0] == "DELETE"]
check("delete by uid hits only that card", len(dels) == 1 and dels[0][1] == "/carddav/u/a/2.vcf", dels)
check("delete result names book", r == {"deleted": True, "uid": "UID-2",
                                        "full_name": "Max Mustermann", "addressbook": "Kontakte"}, r)

# --- injection through type and line breaks
calls.clear()
for t, want in (("Privat: Handy", "TEL;TYPE=PRIVAT;TYPE=HANDY:1"),
                ("work\r\nPHOTO;VALUE=URI:http://x", "TEL;TYPE=WORK;TYPE=PHOTO;TYPE=VALUEURIHTTP;TYPE=X:1"),
                ("a;b", "TEL;TYPE=A;TYPE=B:1"), ('x"y', "TEL;TYPE=XY:1"),
                ("cell, voice", "TEL;TYPE=CELL;TYPE=VOICE:1"),
                ("Büro (Zentrale)", "TEL;TYPE=BÜRO;TYPE=ZENTRALE:1")):
    uid = srv.create_contact(full_name="T", phones=[F(value="1", type=t)])["uid"]
    lines = store[f"/carddav/u/a/{uid}.vcf"].split("\r\n")
    check(f"type {t!r} cannot inject, written as one TEL line",
          want in lines and sum(x.startswith(("TEL", "PHOTO")) for x in lines) == 1, lines)
uid = srv.create_contact(full_name="E", emails=[F(value="e@x.test", type="internet, home")])["uid"]
check("email type 'internet, home' -> INTERNET once", "EMAIL;TYPE=INTERNET;TYPE=HOME:e@x.test"
      in store[f"/carddav/u/a/{uid}.vcf"].split("\r\n"), store[f"/carddav/u/a/{uid}.vcf"])
srv.update_contact(uid, phones=[F(value="2", type="cell, voice"), F(value="3", type="Büro (Zentrale)")])
check("update echoing get_contact types works", "TEL;TYPE=CELL;TYPE=VOICE:2" in store[f"/carddav/u/a/{uid}.vcf"])
srv.update_contact("UID-4", note="Hotline\rTEL:+49 900 666", job_title="a\r\nb\x00c")
lines = store["/carddav/u/b/4.vcf"].split("\r\n")
check("CR in note stays inside the note", "NOTE:Hotline\\nTEL:+49 900 666" in lines
      and not any(x.startswith("TEL") for x in lines), lines)
check("CRLF escaped, NUL dropped", "TITLE:a\\nbc" in lines, lines)
check("... read back as one note, no extra phone", "phones" not in srv.get_contact("UID-4"), srv.get_contact("UID-4"))

# --- FN is never dropped
srv.update_contact("UID-4", full_name="")
check("full_name='' keeps the FN", "FN:Hans Meier" in store["/carddav/u/b/4.vcf"], store["/carddav/u/b/4.vcf"])
store["/carddav/u/c/9.vcf"] = card("UID-9", "Max Muster").replace("N:;;;;", "N:Muster;Max;;;")
store["/carddav/u/c/10.vcf"] = card("UID-10", "Firma X", "ORG:Firma X GmbH;")
c._cache.clear()
srv.update_contact("UID-9", first_name="", last_name="")
check("empty first+last keeps the FN", "FN:Max Muster" in store["/carddav/u/c/9.vcf"], store["/carddav/u/c/9.vcf"])
srv.update_contact("UID-10", full_name=" ")
check("blank FN falls back to the organisation", "FN:Firma X GmbH" in store["/carddav/u/c/10.vcf"])
srv.update_contact("UID-10", full_name="\x01")
check("control-only FN counts as blank", "FN:Firma X GmbH" in store["/carddav/u/c/10.vcf"]
      and "FN:\r\n" not in store["/carddav/u/c/10.vcf"], store["/carddav/u/c/10.vcf"])
r = srv.create_contact(full_name="   ", first_name="Max", last_name="Neu")
check("create: blank full_name -> first + last", r["full_name"] == "Max Neu"
      and "FN:Max Neu" in store[f"/carddav/u/a/{r['uid']}.vcf"], r)
store["/carddav/u/c/9.vcf"] = card("UID-9", "Max Muster").replace("N:;;;;", "N:Muster;Max;;;")
c._cache.clear()
srv.update_contact("UID-9", first_name="Moritz")
check("new first name rebuilds FN", "FN:Moritz Muster" in store["/carddav/u/c/9.vcf"], store["/carddav/u/c/9.vcf"])
check("contact still listed", srv.get_contact("UID-9")["full_name"] == "Moritz Muster")

# --- a display name the user chose is not overwritten by a new first name
store["/carddav/u/c/13.vcf"] = card("UID-13", "ACME GmbH", "ORG:ACME GmbH;")
store["/carddav/u/c/14.vcf"] = card("UID-14", "Dr. Anna Müller").replace("N:;;;;", "N:Müller;Anna;;;")
c._cache.clear()
srv.update_contact("UID-13", first_name="Hans")
check("company name kept when a first name is added",
      "FN:ACME GmbH" in store["/carddav/u/c/13.vcf"]
      and "N:;Hans;;;" in store["/carddav/u/c/13.vcf"], store["/carddav/u/c/13.vcf"])
srv.update_contact("UID-14", first_name="Berta")
check("name with a title kept", "FN:Dr. Anna Müller" in store["/carddav/u/c/14.vcf"],
      store["/carddav/u/c/14.vcf"])
check("plain name still follows the first name",
      "FN:Moritz Muster" in store["/carddav/u/c/9.vcf"], store["/carddav/u/c/9.vcf"])

# --- a name of blanks is no name
calls.clear()
for kw in ({"full_name": "   "}, {"organization": "\x01"}, {"first_name": " ", "last_name": ""}):
    msg = raises(srv.create_contact, **kw)
    check(f"create{tuple(kw)} without a real name refused", msg and "at least" in msg, msg)
check("... and nothing written", not [x for x in writes() if x[0] == "PUT"], writes())

# --- a response without an href is skipped instead of breaking the lookup
EXTRA["response"] = ('<d:response><d:href/><d:propstat><d:prop><d:getetag>"x"</d:getetag>'
                     "<c:address-data>" + escape(card("UID-X", "Ohne Href")) + "</c:address-data>"
                     "</d:prop></d:propstat></d:response>")
c._cache.clear()
check("list survives a response without an href", srv.list_contacts()["total"] >= 1)
check("lookup survives it too", srv.get_contact("Hans Meier")["uid"] == "UID-4")
check("the nameless entry is not offered", "No contact matching" in (raises(srv.get_contact, "Ohne Href") or ""))
EXTRA.clear()

# --- an expired DSM session must not overwrite a contact
before = store["/carddav/u/b/4.vcf"]
EXTRA["session_over"] = True
calls.clear()
msg = raises(srv.update_contact, "UID-4", note="neuer Text")
check("session gone: update refused", msg and "session" in msg.lower(), msg)
check("... and nothing written", store["/carddav/u/b/4.vcf"] == before
      and not [x for x in calls if x[0] == "PUT"], calls)
msg = raises(srv.delete_contact, "UID-4")
check("session gone: delete does not report success",
      msg and "/carddav/u/b/4.vcf" in store, (msg, list(store)))
EXTRA.clear()
c._cache.clear()

# --- ETags: missing or weak means "must still exist", never a broken If-Match
for kind, expect in (("none", "*"), ("weak", "*")):
    EXTRA["etag"] = kind
    calls.clear()
    srv.update_contact("UID-4", note=f"etag {kind}")
    puts = [x for x in calls if x[0] == "PUT"]
    check(f"{kind} ETag -> If-Match {expect}", puts and puts[0][2] == expect, puts)
    check(f"... and the change is written ({kind})", f"NOTE:etag {kind}" in store["/carddav/u/b/4.vcf"])
    EXTRA.clear()
    c._cache.clear()

# --- the NAS pointing somewhere else is refused
foreign = srv.Client()
foreign._http = httpx.Client(transport=httpx.MockTransport(handler))
msg = raises(foreign.abs, "https://evil.example/p/")
check("address on another host refused", msg and "somewhere else" in msg, msg)
check("... but the caller's own text may be compared",
      foreign.abs("https://evil.example/p/", check=False) == "https://evil.example/p/")
check("... and an address on the NAS still works",
      foreign.abs("/carddav/u/a/1.vcf") == ORIGIN + "/carddav/u/a/1.vcf")

# --- counting a book does not download it
calls.clear()
books = srv.list_addressbooks()
want = [len([h for h in store if h.startswith(href)]) for href, _ in BOOKS]
check("book sizes counted", [b["contacts"] for b in books] == want, (books, want))
check("... without a single REPORT", not [x for x in calls if x[0] == "REPORT"], calls)

# --- several notes, address parts, phone search
store["/carddav/u/c/15.vcf"] = card("UID-15", "Viel Text", "NOTE:erste Notiz", "NOTE:zweite Notiz",
                                    "ADR;TYPE=home:Postfach 42;c/o Frau Meier;Weg 1;Ort;;12345;DE",
                                    "TEL:0171 123", "TEL:4567 890")
c._cache.clear()
g = srv.get_contact("UID-15")
check("both notes shown", g.get("note") == "erste Notiz\nzweite Notiz", g.get("note"))
check("post box and extended address shown", g["addresses"][0].get("po_box") == "Postfach 42"
      and g["addresses"][0].get("extended") == "c/o Frau Meier", g["addresses"])
check("phone search does not run numbers together",
      srv.search_contacts("1234567")["matches"] == 0, srv.search_contacts("1234567"))
check("phone search still finds one number", srv.search_contacts("4567 890")["matches"] == 1)

# --- a label without its property is dropped
store["/carddav/u/c/16.vcf"] = card("UID-16", "Label", "item1.TEL:0171 1",
                                    "item1.X-ABLabel:Ferienhaus", "item2.EMAIL:a@b.test",
                                    "item2.X-ABLabel:Privat")
c._cache.clear()
srv.update_contact("UID-16", phones=[F(value="0171 2", type="home")])
lines = store["/carddav/u/c/16.vcf"].split("\r\n")
check("label of the replaced phone gone, the email's label kept",
      not any("Ferienhaus" in x for x in lines) and any("Privat" in x for x in lines), lines)

# --- paging edge cases
check("negative offset counts from the start", srv.list_contacts(offset=-1)["returned"] >= 1)

# --- reads are not followed anywhere either
EXTRA["redirect_read"] = True
c._cache.clear()
calls.clear()
msg = raises(srv.update_contact, "UID-4", note="von woanders")
check("a read answered with a redirect is refused", msg and "redirected" in msg, msg)
check("... and nothing was written", not [x for x in writes() if x[0] == "PUT"], writes())
msg = raises(srv.get_contact, "UID-4")
check("... the same for looking one up", msg and "redirected" in msg, msg)
EXTRA.clear()
c._cache.clear()

# --- a weak ETag does not silently lose someone else's change
EXTRA["etag"] = "weak"
EXTRA["changes_behind_us"] = True
before = store["/carddav/u/b/4.vcf"]
msg = raises(srv.update_contact, "UID-4", note="meins")
check("weak ETag + change in between: refused, not overwritten",
      msg and "changed on the server" in msg, msg)
check("... the other change is still there", "TEL:0170 NEU" in store["/carddav/u/b/4.vcf"],
      store["/carddav/u/b/4.vcf"])
EXTRA.clear()
c._cache.clear()

# --- a label stays with the property it belongs to
store["/carddav/u/c/17.vcf"] = card("UID-17", "Gruppen",
                                    "item1.ADR;TYPE=home:;;Weg 1;Ort;;12345;DE",
                                    "item1.X-ABADR:de", "item1.X-ABLabel:Zuhause",
                                    "item1.TEL:0171 1")
c._cache.clear()
srv.update_contact("UID-17", phones=[F(value="0171 2", type="home")])
kept = store["/carddav/u/c/17.vcf"]
check("address keeps its label when the phone is replaced",
      "item1.X-ABLabel:Zuhause" in kept and "item1.X-ABADR:de" in kept
      and "item1.ADR" in kept and "TEL:0171 1" not in kept, kept)

# --- counting counts contacts, not other things
EXTRA["response"] = ("<d:response><d:href>/carddav/u/c/unterordner/</d:href></d:response>"
                     "<d:response><d:href>/carddav/u/c/notiz.txt</d:href></d:response>")
c._cache.clear()
calls.clear()
books = {b["href"]: b["contacts"] for b in srv.list_addressbooks()}
check("only cards are counted",
      books["/carddav/u/c/"] == len([h for h in store if h.startswith("/carddav/u/c/")]), books)
EXTRA.clear()
srv.list_contacts(addressbook="/carddav/u/c/")
calls.clear()
srv.list_addressbooks()
check("a fresh listing is not counted again",
      not [x for x in calls if x[1].startswith("/carddav/u/c/")], calls)

# --- a name with a title follows the name parts
store["/carddav/u/c/18.vcf"] = card("UID-18", "Dr. Anna Müller").replace("N:;;;;", "N:Müller;Anna;;Dr.;")
store["/carddav/u/c/19.vcf"] = card("UID-19", "Müller, Anna").replace("N:;;;;", "N:Müller;Anna;;;")
c._cache.clear()
srv.update_contact("UID-18", last_name="Schmidt")
check("title kept, name follows", "FN:Dr. Anna Schmidt" in store["/carddav/u/c/18.vcf"],
      store["/carddav/u/c/18.vcf"])
srv.update_contact("UID-19", first_name="Berta")
check("'Last, First' follows too", srv.get_contact("UID-19")["full_name"] == "Müller, Berta",
      store["/carddav/u/c/19.vcf"])

# --- a card that claims an address off the NAS is skipped
EXTRA["response"] = ('<d:response><d:href>https://evil.test/x.vcf</d:href><d:propstat><d:prop>'
                     '<d:getetag>"x"</d:getetag><c:address-data>'
                     + escape(card("UID-EVIL", "Fremd")) + "</c:address-data></d:prop></d:propstat></d:response>")
c._cache.clear()
check("a card from another host is not listed", srv.search_contacts("Fremd")["matches"] == 0)
check("... and the others are still found", srv.get_contact("Hans Meier")["uid"] == "UID-4")
EXTRA.clear()
c._cache.clear()

# --- deleting without a version mark checks it is still the same contact
EXTRA["etag"] = "none"
srv.get_contact("UID-13")
store["/carddav/u/c/13.vcf"] = card("UID-ANDERS", "Jemand anderes")
msg = raises(srv.delete_contact, "UID-13")
check("the card at that address changed: not deleted", msg and "no longer holds" in msg
      and "/carddav/u/c/13.vcf" in store, msg)
EXTRA.clear()
c._cache.clear()

# --- vCard 4.0 stays 4.0
store["/carddav/u/c/11.vcf"] = card("UID-11", "Vier", version="4.0")
c._cache.clear()
srv.update_contact("UID-11", note="n")
check("VERSION 4.0 kept", store["/carddav/u/c/11.vcf"].split("\r\n")[1] == "VERSION:4.0")

# --- writes do not follow redirects
store["/carddav/u/c/moved.vcf"] = card("UID-R", "Umzug")
c._cache.clear(); calls.clear()
msg = raises(srv.delete_contact, "UID-R")
check("DELETE answered with 302 is an error", msg and "redirected" in msg, msg)
check("... no GET behind it, card still there",
      [x[0] for x in calls if x[1].startswith("/somewhere")] == [] and "/carddav/u/c/moved.vcf" in store, calls)

# --- 412 clears the cache, so the retry works
srv.get_contact("UID-6")
store["/carddav/u/c/6.vcf"] = card("UID-6", "Anna Schmidt", "NOTE:changed elsewhere")
msg = raises(srv.delete_contact, "UID-6")
check("stale ETag gives 412", msg and "412" in msg, msg)
check("retry after 412 succeeds", srv.delete_contact("UID-6")["deleted"])

# --- photos are not kept in the cache, but survive an update
store["/carddav/u/c/12.vcf"] = card("UID-12", "Foto", PHOTO)
c._cache.clear()
g = srv.get_contact("UID-12")
cached = [i["raw"] for i in c._cache[ORIGIN + "/carddav/u/c/"][1] if i["href"].endswith("12.vcf")][0]
check("cache holds no photo blob", len(cached) < 300 and g.get("has_photo") is True, (len(cached), g))
srv.update_contact("UID-12", note="mit Foto")
check("update keeps the photo on the server", "A" * 30000 in store["/carddav/u/c/12.vcf"].replace("\r\n ", ""))

# --- more than ten candidates are cut off with a hint
reset()
for i in range(12):
    store[f"/carddav/u/c/x{i}.vcf"] = card(f"X{i}", "Peter Pan")
fresh_client()
msg = raises(srv.delete_contact, "Peter Pan")
check("12 namesakes: 10 listed + hint", msg and "fits 12 contacts" in msg
      and msg.count("\n- Peter Pan") == 10 and "2 more" in msg, msg)
check("... no DELETE", not writes(), writes())

# --- legacy URL with credentials, privileges under 404
srv.BASE_URL = "https://user:pw@nas.test:5001/carddav/"
check("user:pass stripped from legacy URL", srv.Client().base == "https://nas.test:5001/carddav/")
srv.BASE_URL = ""
srv.HOST = "nas.local:50o1"
bad = srv.Client()
bad._http = httpx.Client(transport=httpx.MockTransport(handler))
msg = raises(bad.addressbooks)
check("typo in the port is a readable error", msg and "not a valid address" in msg, msg)
srv.HOST = "nas.test"


def dav(request):
    if request.method != "PROPFIND":
        return httpx.Response(500)
    p = request.url.path
    ms = '<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">{}</d:multistatus>'
    if p == "/carddav/":
        r = ('<d:response><d:href>/carddav/</d:href><d:propstat><d:prop><d:current-user-principal>'
             '<d:href>/carddav/u/</d:href></d:current-user-principal></d:prop>'
             '<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>')
    elif request.headers.get("Depth") == "0":
        r = ('<d:response><d:href>/carddav/u/</d:href><d:propstat><d:prop><c:addressbook-home-set>'
             '<d:href>/carddav/u/</d:href></c:addressbook-home-set></d:prop>'
             '<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>')
    else:
        r = ('<d:response><d:href>/carddav/u/a/</d:href>'
             '<d:propstat><d:prop><d:resourcetype><d:collection/><c:addressbook/></d:resourcetype>'
             '<d:displayname>Ohne ACL</d:displayname></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>'
             '<d:propstat><d:prop><d:current-user-privilege-set/></d:prop>'
             '<d:status>HTTP/1.1 404 Not Found</d:status></d:propstat></d:response>'
             '<d:response><d:href>/carddav/u/b/</d:href>'
             '<d:propstat><d:prop><d:resourcetype><d:collection/><c:addressbook/></d:resourcetype>'
             '<d:displayname>Nur lesen</d:displayname><d:current-user-privilege-set><d:privilege><d:read/>'
             '</d:privilege></d:current-user-privilege-set></d:prop><d:status>HTTP/1.1 200 OK</d:status>'
             '</d:propstat></d:response>')
    return httpx.Response(207, content=ms.format(r).encode())


d = srv.Client()
d._http = httpx.Client(transport=httpx.MockTransport(dav))
books = {b["name"]: b["writable"] for b in d.addressbooks()}
check("privileges under 404 = unknown, not read-only", books == {"Ohne ACL": None, "Nur lesen": False}, books)

print("\nFAILED" if failures else "\nall passed", failures)
sys.exit(1 if failures else 0)
