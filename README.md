# synology-contacts-mcp

[![Claude Desktop](https://img.shields.io/badge/Claude%20Desktop-extension-d97757.svg)](#)
[![Protocol](https://img.shields.io/badge/protocol-CardDAV-0b7285.svg)](#)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.10-3776ab.svg?logo=python&logoColor=white)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Donate](https://img.shields.io/badge/Donate-PayPal-00457C.svg?logo=paypal)](https://www.paypal.com/donate/?hosted_button_id=6CDEVZGJWTNQQ)

Claude Desktop extension that gives Claude access to the **Contacts app of a Synology NAS** — or to any other CardDAV server. Search the address book, read a contact in full, create, change and delete entries, straight from a conversation. Ships as a single `.mcpb` file.

The one thing it deliberately refuses to do: hand over **photo blobs**. A single Synology vCard can carry a 30 KB base64 JPEG, and sixty of them would fill the model's context with nothing useful. `has_photo: true` is all you get, and that is the point.

Setup asks for a **host name and a protocol switch**, never a URL. The CardDAV path is appended by the extension — getting that path wrong is what makes DSM answer with its login page, and the resulting `undefined entity: line 7, column 0` explains nothing to anybody.

See also **[synology-calendar-mcp](https://github.com/sorglos-it/synology-calendar-mcp)** — the same idea for calendars and todos over CalDAV — and **[github-mcp](https://github.com/sorglos-it/github-mcp)** for repositories on github.com.

## Features

- **Read** — list address books with contact counts, list contacts, full-text search across name, organisation, email, phone and note, fetch one contact in detail
- **Write** — create, update and delete contacts, with several phone numbers, email addresses, organisation, birthday and categories
- **Partial updates keep the rest** — an update rewrites only the fields you pass; photo, custom `X-` properties and Apple label groups survive untouched
- **Phone search that works** — searching `017012345678` also finds `+49 170 1234 5678`; digits are compared, formatting is ignored
- **Apple label groups understood** — `item1.TEL` + `item1.X-ABLabel:_$!<Home>!$_` is read back as a plain `home`
- **Read-only books are marked** — `writable` comes from the server's own privilege set, so a shared team book is visible as such before a write fails
- **No URL to get right** — host name plus an HTTPS switch; ports default to the DSM values 5001 (https) and 5000 (http)
- **Runs locally** — the server talks to your NAS directly; nothing is sent to a third party
- **No credentials in the package** — Claude Desktop stores the password in the OS keychain

## Requirements

- Claude Desktop 0.10.0 or newer (Windows, macOS, Linux)
- [uv](https://docs.astral.sh/uv/) on the target machine — it fetches Python and the dependencies on first start
- A reachable Synology NAS with the Contacts app installed
- A DSM user account that owns the address books

```powershell
winget install --id=astral-sh.uv -e
```

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart Claude Desktop afterwards, otherwise it will not see the new `PATH` and the extension stops at "server disconnected".

## Installation

1. Grab `synology-contacts-1.1.2.mcpb` from [Releases](https://github.com/sorglos-it/synology-contacts-mcp/releases), or build it yourself (see below).
2. Claude Desktop → **Settings → Extensions → Advanced settings → Install extension…**, pick the file. Drag and drop onto the extensions window works too.
3. Fill in the fields (see next section) and enable the extension. The first start takes a moment while uv resolves dependencies.
4. Ask Claude something like *"which address books do I have?"*.

## Configuration

| Field | Meaning |
|---|---|
| **NAS-Adresse** | Host name or IP only, e.g. `nas.example.com`. No `https://`, no path. A non-standard port goes here as `nas.example.com:8443`. |
| **HTTPS verwenden** | On → `https`, default port 5001. Off → `http`, default port 5000. These are the DSM defaults. |
| **Benutzername** | DSM login name of the user who owns the address books |
| **Passwort** | DSM password; stored in the OS keychain, never in the package |
| **Zertifikat prüfen** | Leave **off** while the NAS uses its self-signed certificate. Turn on for a real certificate (e.g. Let's Encrypt). |

The labels are German because the extension manifest is; the fields behave exactly as described above.

## Tools

| Tool | Purpose |
|---|---|
| `list_addressbooks` | Address books with contact count and `writable` flag |
| `list_contacts` | List contacts, `limit` / `offset` |
| `search_contacts` | Search name, organisation, email, phone, note |
| `get_contact` | One contact in full, by UID, href or display name |
| `create_contact` | New contact |
| `update_contact` | Change only the fields passed |
| `delete_contact` | Delete a contact |

## How it works

1. Claude Desktop starts `server/server.py` through `uv run --script`; dependencies live in the PEP 723 header of that one file.
2. The server builds the endpoint from host name and protocol switch and appends `/carddav/`.
3. Discovery follows the CardDAV chain — `current-user-principal`, then `addressbook-home-set`, then a `Depth: 1` PROPFIND for the books, including `current-user-privilege-set` to tell writable books from read-only ones.
4. Contacts are fetched per book with a single `addressbook-query` REPORT and cached for 60 seconds, so a search does not re-download the whole book.
5. vCards are parsed in-house (line unfolding, escaped separators, quoted parameters) and reduced to compact JSON. Writes go back as vCard 3.0 with `If-Match` / `If-None-Match`, so a concurrent change fails loudly instead of silently overwriting.

`CARDDAV_BASE_URL` is still honoured if you set a complete URL directly, and wins over the host name.

## Environment variables

Useful if you want to run the server standalone rather than as an extension:

| Variable | Meaning |
|---|---|
| `CARDDAV_HOST` | Host name, `:port` optional |
| `CARDDAV_HTTPS` | `true` (default) → https + port 5001, `false` → http + port 5000 |
| `CARDDAV_USERNAME` | DSM login name |
| `CARDDAV_PASSWORD` | DSM password |
| `CARDDAV_VERIFY_SSL` | `false` for a self-signed certificate |
| `CARDDAV_TIMEOUT` | Seconds per HTTP request, default `45`. Blank or unparsable falls back to the default. |
| `CARDDAV_BASE_URL` | Legacy: a complete endpoint URL, wins over `CARDDAV_HOST` |

```bash
uv run --script server/server.py
```

## Notes & caveats

- **Shared address books are often read-only.** Synology hands out team books without write privileges; `list_addressbooks` shows `writable: false` for them, and a write attempt returns HTTP 403.
- **Photos are never returned, and never written.** An update preserves an existing `PHOTO` untouched, but there is no way to set one through this extension.
- **`delete_contact` is permanent.** There is no CardDAV trash. Identify by UID rather than by name; an ambiguous name is rejected rather than guessed.
- **Certificate checking off disables TLS verification** for the connection. Right for a self-signed NAS on your own LAN, wrong over the open internet.
- **The German field labels are not a bug**, just the language the manifest was written in.
- **DSM is slow to authenticate.** The first authenticated request of a session regularly takes five seconds or more — in clean five-second steps, which is what a lookup running into its own timeout looks like — while later ones come from its session cache in milliseconds. The default of 45 s absorbs that. The **Zeitlimit pro Anfrage** field raises it for a NAS that needs even longer — it arrives as `CARDDAV_TIMEOUT`, in seconds — but a NAS behaving this way is worth a look on the DSM side. A blank or unparsable value falls back to 45 rather than stopping the server.

## Building the .mcpb yourself

```bash
npx @anthropic-ai/mcpb pack . synology-contacts-1.1.2.mcpb
```

There is nothing to compile — `server/server.py` carries its dependencies in a PEP 723 header and uv resolves them at first start.

## Support this project ❤️

If this extension saved you time, you can support further development:

[![Donate with PayPal](https://www.paypalobjects.com/en_US/i/btn/btn_donate_LG.gif)](https://www.paypal.com/donate/?hosted_button_id=6CDEVZGJWTNQQ)

**[➡️ Donate via PayPal](https://www.paypal.com/donate/?hosted_button_id=6CDEVZGJWTNQQ)**

## License

This project is licensed under the [MIT License](LICENSE) — © 2026 Thomas Weirich.
