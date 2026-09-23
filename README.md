# synology-contacts-mcp

[![Claude Desktop](https://img.shields.io/badge/Claude%20Desktop-extension-d97757.svg)](#)
[![Protocol](https://img.shields.io/badge/protocol-CardDAV-0b7285.svg)](#)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.10-3776ab.svg?logo=python&logoColor=white)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Donate](https://img.shields.io/badge/Donate-PayPal-00457C.svg?logo=paypal)](https://www.paypal.com/donate/?hosted_button_id=6CDEVZGJWTNQQ)

Lets **Claude** use the **Contacts app of your Synology NAS**: search the address book,
read a contact in full, create, change and delete entries, straight from a conversation. A Claude Desktop extension
in a single `.mcpb` file that runs on your computer and talks to your NAS only. Other CardDAV servers work only if
they use the same path, `/carddav/` — it is fixed, not detected.

| Folder | Purpose | Language | Start | Build |
|---|---|---|---|---|
| `apps/server` | MCP server: Claude's contact tools over CardDAV | Python ≥ 3.10, run by uv | install the `.mcpb` in Claude Desktop | `python tools/build.py` → `dist/synology-contacts-<version>.mcpb` |

## Start in 3 steps

You need Claude Desktop 0.10.0 or newer (Windows, macOS, Linux), [uv](#install-uv), a reachable Synology NAS with the
Contacts app installed, and the DSM user account that owns the address books.

1. **Get the extension** `synology-contacts-<version>.mcpb` from
   [Releases](https://github.com/sorglos-it/synology-contacts-mcp/releases/latest), or build it yourself:
   `python tools/build.py`.
2. **Install it in Claude Desktop:** *Settings → Extensions → Advanced settings → Install extension…*, pick the file.
   Dragging it onto the extensions window works too.
3. **Fill in the fields** (see [Configuration](#configuration)) and switch the extension on. Then ask Claude
   something like *"which address books do I have?"*. The first start takes a moment while uv fetches the
   dependencies.

## Update

Install the new `.mcpb` the same way; it replaces the old one.
**Once, for bundles built after 22 Sep 2026:** the author name changed, so Claude Desktop sees a new
extension. Uninstall the old *Synology Contacts* extension first (*Settings → Extensions*), then install the new one and fill in the fields again.
**From 1.1.3, *Zertifikat prüfen* is on.** A NAS still on its self-signed certificate needs a valid one (DSM →
*Control Panel → Security → Certificate*, e.g. Let's Encrypt), or the switch goes off — on your own network only.

## Install uv

uv fetches Python and the two libraries the server needs (`mcp`, `httpx`) on first start, so the `.mcpb` stays a few
KB small and runs on every platform. Shipping the libraries instead is not an option: `mcp` pulls in `pydantic-core`,
a compiled extension tied to one Python version and platform, and on Windows `pywin32` would blow the package up to
over 800 MB.

```powershell
winget install --id=astral-sh.uv -e
```

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

The first line is for Windows, the second for macOS and Linux. **Restart Claude Desktop afterwards**, otherwise it does
not see the new `PATH` and the extension stops at "server disconnected".

## Configuration

| Field | Meaning |
|---|---|
| **NAS-Adresse** | Host name or IP only, e.g. `nas.example.com`. No `https://`, no path. A non-standard port goes here as `nas.example.com:8443`. |
| **HTTPS verwenden** | On → `https`, default port 5001. Off → `http`, default port 5000, and the password crosses the network unencrypted. These are the DSM defaults. |
| **Benutzername** | DSM login name of the user who owns the address books |
| **Passwort** | DSM password; stored in the OS keychain, never in the package |
| **Zertifikat prüfen** | **On** by default: the extension only talks to a NAS whose certificate is valid for the name entered above, so nobody in between can pose as the NAS and read the password. Needs a real certificate on the NAS (e.g. Let's Encrypt). Switch off only for a NAS on its self-signed certificate, and only on your own network. |
| **Zeitlimit pro Anfrage** | Seconds allowed per request, default 45. Leave it alone unless the NAS is slow enough to run into it. |

The labels are German because the extension manifest is; the fields behave exactly as described above.

Setup asks for a **host name and a protocol switch**, never a URL. The CardDAV path is appended by the extension —
getting that path wrong is what makes DSM answer with its login page, and the resulting
`undefined entity: line 7, column 0` explains nothing to anybody.

## Tools

| Tool | Purpose |
|---|---|
| `list_addressbooks` | Address books with contact count and `writable` flag |
| `list_contacts` | List contacts, `limit` / `offset` |
| `search_contacts` | Search name, organisation, email, phone, note |
| `get_contact` | One contact in full, by UID, href or display name |
| `create_contact` | New contact, with several phone numbers, email addresses, organisation, birthday and categories |
| `update_contact` | Change only the fields passed; emails, phones and url replace every entry of that kind |
| `delete_contact` | Delete a contact |

- **Partial updates keep the rest** — an update rewrites only the fields you pass; photo, custom `X-` properties and
  Apple label groups survive untouched.
- **Phone search ignores formatting** — with four or more digits in the query, the digits are compared as a piece of
  the number: `170 1234-5678` finds `+49 170 1234 5678` and `0170 12345678`. `0` and `+49` are not converted into
  each other, so `017012345678` does not find `+49 170 1234 5678` — leave out the leading `0` or `+49`.
- **Apple label groups understood** — `item1.TEL` + `item1.X-ABLabel:_$!<Home>!$_` is read back as a plain `Home`.
- **Read-only books are marked** — `writable` comes from the server's own privilege set, so a shared team book is
  visible as such before a write fails.
- **No photo blobs.** A single Synology vCard can carry a 30 KB base64 JPEG, and sixty of them would fill the model's
  context with nothing useful. `has_photo: true` is all you get, and that is the point.

## Security and caveats

- **Runs locally.** The server talks to your NAS directly; nothing is sent to a third party. Claude Desktop stores the
  password in the OS keychain — there are no credentials in the package.
- **Certificate checking protects the password.** It is on by default since 1.1.3. Switched off, anyone between your
  computer and the NAS can pose as the NAS and read the DSM password — acceptable on your own LAN with a self-signed
  NAS, wrong anywhere else. Certificates trusted by the operating system count too. A rejected certificate is reported
  with the reason and both ways out.
- **Namesakes are never guessed.** When a name fits several contacts — the same name twice, or a name that is also
  part of others (*Müller* next to *Anna Müller*) — `get_contact`, `update_contact` and `delete_contact` stop and list
  the candidates with address book, company, first email and phone. Claude then asks which one you mean and continues
  with its UID. The same goes for one UID in two address books and for two address books with the same name.
- **`delete_contact` is permanent.** There is no CardDAV trash.
- **Shared address books are often read-only.** Synology hands out team books without write privileges;
  `list_addressbooks` shows `writable: false` for them, and a write attempt returns HTTP 403.
- **Photos are never returned, and never written.** An update preserves an existing `PHOTO` untouched, but there is no
  way to set one through this extension.
- **DSM is slow to authenticate.** The first authenticated request of a session regularly takes five seconds or more —
  in clean five-second steps, which is what a lookup running into its own timeout looks like — while later ones come
  from its session cache in milliseconds. The default of 45 s absorbs that. The **Zeitlimit pro Anfrage** field raises
  it for a NAS that needs even longer — it arrives as `CARDDAV_TIMEOUT`, in seconds — but a NAS behaving this way is
  worth a look on the DSM side. A blank or unparsable value falls back to 45 rather than stopping the server.

## Development

```text
apps/server/manifest.json    name, settings and start command of the extension (mcpb format)
apps/server/server.py        the server; its dependencies sit in the PEP 723 header of this one file
apps/server/assets/icon.png  icon shown in Claude Desktop
apps/server/VERSION          version, the same as in manifest.json
apps/server/tests/           checks against fake servers; never contact a real NAS and never ship in the bundle
tools/build.py               packs apps/server, README.md and LICENSE into dist/synology-contacts-<version>.mcpb
```

```bash
cd apps/server/tests
uv run --with "mcp>=2.0,<3" --with httpx python test_contacts.py   # the tools against a fake address book
uv run --with cryptography python test_mcp.py                      # the same over real MCP, as Claude sees it
uv run --with "mcp>=2.0,<3" --with httpx --with cryptography python test_tls.py   # certificate checking
```

Every check prints one line and the run ends with `all passed`. `test_tls.py` also checks the calendar extension
when `synology-calendar-mcp` sits next to this folder.

```bash
python tools/build.py        # Python 3.8 or newer, nothing else; `uv run tools/build.py` works too
```

There is nothing to compile. For a new version, raise it in `apps/server/VERSION` and `apps/server/manifest.json` —
the build stops when the two differ.

How it works:

1. Claude Desktop starts `server.py` through `uv run --script`; the dependencies live in the PEP 723 header of that one
   file.
2. The server builds the endpoint from host name and protocol switch and appends `/carddav/`.
3. Discovery follows the CardDAV chain — `current-user-principal`, then `addressbook-home-set`, then a `Depth: 1`
   PROPFIND for the books, including `current-user-privilege-set` to tell writable books from read-only ones.
4. Contacts are fetched per book with a single `addressbook-query` REPORT and cached for 60 seconds without their
   photos, so a search does not re-download the whole book.
5. vCards are parsed in-house (line unfolding, escaped separators, quoted parameters) and reduced to compact JSON.
   Writes go back as vCard 3.0 with `If-Match` / `If-None-Match`, so a concurrent change fails loudly instead of
   silently overwriting.

To run the server without Claude Desktop, set the variables and start it from the project folder:

```bash
uv run --script apps/server/server.py
```

| Variable | Meaning |
|---|---|
| `CARDDAV_HOST` | Host name, `:port` optional |
| `CARDDAV_HTTPS` | `true` (default) → https + port 5001, `false` → http + port 5000 |
| `CARDDAV_USERNAME` | DSM login name |
| `CARDDAV_PASSWORD` | DSM password |
| `CARDDAV_VERIFY_SSL` | `true` (default) checks the certificate; `false` only for a self-signed NAS on your own network |
| `CARDDAV_TIMEOUT` | Seconds per HTTP request, default `45`. Blank or unparsable falls back to the default. |
| `CARDDAV_BASE_URL` | Legacy: a complete endpoint URL, wins over `CARDDAV_HOST` |

See also **[synology-calendar-mcp](https://github.com/sorglos-it/synology-calendar-mcp)** — the same idea for calendars
and todos over CalDAV — and **[github-mcp](https://github.com/sorglos-it/github-mcp)** for repositories on github.com.
Changes: [CHANGELOG.md](CHANGELOG.md).

## License

This project is licensed under the [MIT License](LICENSE) — © 2026 Sorglos Thomas Weirich.

## Donate via PayPal

If this extension saved you time, you can support further development:

[![Donate with PayPal](https://www.paypalobjects.com/en_US/i/btn/btn_donate_LG.gif)](https://www.paypal.com/donate/?hosted_button_id=6CDEVZGJWTNQQ)

**[➡️ Donate via PayPal](https://www.paypal.com/donate/?hosted_button_id=6CDEVZGJWTNQQ)**
