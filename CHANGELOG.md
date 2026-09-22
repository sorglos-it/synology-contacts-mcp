# Changelog

## 1.1.3 – 2026-09-22

- **Certificate checking is on by default** (*Zertifikat prüfen*): it keeps the DSM password from being intercepted.
  A NAS still on its self-signed certificate needs a valid one (e.g. Let's Encrypt) or the switch turned off — on your
  own network only. A rejected certificate is reported with the reason and both ways out instead of a bare OpenSSL
  message.
  Certificates trusted by the operating system count as well, not only the built-in list.
- **Namesakes are no longer guessed.** A name that fits several contacts — the same name twice, or a name that is also
  part of others (*Müller* next to *Anna Müller*) — used to resolve to the first or the exact match, so
  `delete_contact` could remove the wrong person. `get_contact`, `update_contact` and `delete_contact` now list the
  candidates, and Claude asks which one is meant. The same holds for one UID in two address books and for two address
  books with the same name.
- **Error messages reach Claude.** The MCP SDK passes on only errors of its own `ToolError` type; everything else
  arrived as a bare *"Error executing tool …"*, so Claude could neither name the cause (password, read-only book,
  certificate) nor ask which contact was meant.
- **No more vCard injection:** a phone or email type has to be one word (home, work, cell …), and a line break inside
  any value stays inside that value. Before, a type or note with a line break could add properties of its own.
- An empty identifier is refused. Before, it matched the first contact without a UID or name.
- `update_contact` never removes the display name (FN) — a card without one vanished from every tool. It reads back
  the card it wrote instead of looking it up again, and a vCard 4.0 card stays 4.0.
- A change answered with a redirect is reported as an error instead of passing for a success that never happened.
  After a *412*, the next attempt re-reads the contact instead of sending the stale ETag again.
- Photos are no longer held in the cache (up to 30 KB each). Address books on servers that do not report privileges
  show `writable` as unknown instead of false. A legacy `CARDDAV_BASE_URL` with `user:password@` no longer puts the
  password into error messages.
- The **HTTPS verwenden** description says that switching it off sends the password unencrypted.
- Author is now „Sorglos Thomas Weirich“. Claude Desktop derives the extension's identity from it:
  uninstall the old extension once before installing this version, then enter the settings again.
- Project layout follows the project standard: the server lives in `apps/server/` (`server.py`, `manifest.json`,
  `assets/icon.png`, `VERSION`).
- Inside the `.mcpb`, `server.py` now sits at the top level and the icon in `assets/`; settings and tools are
  unchanged.
- `tools/build.py` builds `dist/synology-contacts-<version>.mcpb` with Python alone — no Node.js or `npx` needed any
  more. The bundle now also carries `LICENSE`.
- README rewritten: start in 3 steps, paths for the new layout, the **Zeitlimit pro Anfrage** field is listed under
  Configuration.

## 1.1.2 – 2026-07-31

- New setting **Zeitlimit pro Anfrage** (seconds, default 45) for a slow NAS; it reaches the server as
  `CARDDAV_TIMEOUT`.

## 1.1.1 – 2026-07-31

- The request timeout can be set through `CARDDAV_TIMEOUT`; a blank or unparsable value falls back to 45 seconds.
  Not published as a release.

## 1.1.0 – 2026-07-31

- First public release. Setup asks for a host name and an HTTPS switch instead of a URL; the CardDAV path is appended
  by the extension.
