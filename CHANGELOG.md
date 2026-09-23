# Changelog

## 1.1.7 – 2026-09-23

- **A redirect is never followed any more, not even when reading.** Answering a read with "look over there" was
  enough to get a contact from somewhere else patched and written back to the NAS - the real card was then gone,
  while the change reported success. Every redirect is now reported instead of followed.
- **A change is no longer lost silently when the NAS gives no usable version mark.** Behind a compressing proxy the
  mark is weak and cannot be used; the contact is read once more directly before writing, and a change someone else
  made in between stops the write instead of being overwritten. Deleting checks that the address still holds the
  same contact.
- A label stays with the property it belongs to: replacing the phone numbers no longer strips an address of its
  label.
- `list_addressbooks` counts only contacts (not folders or other files) and reuses a listing made moments ago
  instead of asking again.
- A display name made from the name parts keeps its spelling when a first or last name changes: *Dr. Anna Müller*
  becomes *Dr. Anna Schmidt*, *Müller, Anna* becomes *Müller, Berta*. A company name is still left alone.
- A card whose address points off the NAS is skipped instead of breaking every lookup.
- The checks that prove all of this live in `apps/server/tests/` now and run against fake servers.

## 1.1.6 – 2026-09-23

- **An ended DSM session can no longer cost a contact.** DSM answers with its login page and HTTP 200; that page was
  taken for the contact, patched and written back, which left the card with almost nothing on it while the tool
  reported success. A reply that is not a contact is refused now, and a change is only "done" when the NAS confirms
  it the way the protocol says.
- **An address the NAS points at somewhere else is refused.** Every request carries the DSM password, so a server
  answering with `https://elsewhere/` as the user's address would have been handed it.
- Without a usable ETag a change now says "the contact has to still exist" instead of overwriting blindly, and a weak
  ETag (behind a compressing proxy, say) no longer makes every change fail.
- A contact with several notes shows all of them; post box and c/o line of an address are shown; a label belonging to
  a replaced phone number or email is removed with it.
- Searching for a number no longer matches across two different numbers, and `list_addressbooks` counts a book
  instead of downloading it (a book of 100 contacts was well over a megabyte).
- The extension no longer picks up a `CARDDAV_BASE_URL` that happens to be set on the computer.

## 1.1.5 – 2026-09-23

- A display name that is not simply first and last name — a company, a name with a title — is no longer overwritten
  when a first or last name is set. `update_contact("ACME GmbH", first_name="Hans")` used to rename the card to
  *Hans*, and the company was gone from every list.
- A name made of blanks or control characters is refused when creating a contact instead of ending up as a card
  called *Unnamed*.
- A server answer without an address for a contact is skipped instead of breaking the next lookup.

## 1.1.4 – 2026-09-22

- Changing the phones or emails of a contact synced from an iPhone works again: types such as *cell, voice* or a label
  like *Büro (Zentrale)* are split into single words instead of being refused, which 1.1.3 did. Characters that could
  break out of the type are still dropped.
- A mistyped port in the NAS address (`nas.local:50o1`) is reported as such instead of a bare *"Error executing
  tool"*.
- A display name of blanks or control characters counts as empty, so the card keeps a real one.

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
