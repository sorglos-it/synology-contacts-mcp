# Changelog

## Unreleased – 2026-09-22

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
