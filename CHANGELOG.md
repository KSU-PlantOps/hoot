# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Logs** tab in the web UI: the live service log (filterable by level, auto-refreshing),
  the persistent event log, and downloads for the service log (`.log`), the event log
  (CSV), and a support bundle zip (config, status, service log, events).
- Download `config.yaml` from Settings, and an advanced editor for the full YAML config —
  sensors, points, web, display — with *Validate* (dry run) before *Save*.
- API: `/api/logs`, `/api/logs/download`, `/api/events`, `/api/events.csv`,
  `GET`/`POST /api/config.yaml` (`?dry_run=true`), `/api/support-bundle.zip`.

### Changed

- README rewritten, with a banner, architecture diagram, concept render, and light/dark
  screenshots of the web UI. The images, and the scripts that regenerate them, are in
  `docs/images/`.

### Fixed

- Reading cards appeared in a different order after each restart (channels were iterated
  through a set). They now follow the driver's channel order.
- The trend chart clipped its last time label.
- The BACnet points table showed calibration offsets without a unit. Offsets are stored in
  °C, so they now say so rather than sitting unlabelled next to a °F value.
- The service log box no longer reserves 420 px when it holds a few lines, and event
  timestamps no longer wrap.

### Security

- `POST /api/restart` now requires a JSON request, so a plain HTML form on another site
  can no longer restart the service from an operator's browser. The new YAML config upload
  accepts only YAML content types for the same reason.

## [1.0.0] - 2026-09-28

First public release.

### Fixed (pre-release review)

- Trend store failed to import on Python 3.11 (Raspberry Pi OS Bookworm) because of
  f-string syntax that needs 3.12+, so `hoot run` crashed at startup.
- A missing or unplugged sensor crashed the service at startup when the hardware library
  raised something other than `SensorError` (for example `ValueError` for a missing I²C
  device). It is now published as a BACnet fault, as intended.
- Hardware drivers leaked an I²C bus reference each time `open()` failed, which was every
  sample cycle while a probe was unplugged.
- A sampling interval changed from the web UI was reported as applied but never took effect.
- An explicit port in `bacnet.address` (`10.1.2.3/24:47809`) was silently dropped.
- The 7-day and 30-day trend views showed only the most recent ~33 hours; long windows are
  now downsampled across the full range.
- Changing the device name, location, or any BACnet setting from the web UI was applied
  without flagging that a restart is needed for BACnet to see it. COV increment changes
  were saved but never applied to the live object.
- Channels from new drivers with units HOOT doesn't know were mislabelled as °F.
- `hoot init` left SCD4x automatic self-calibration at the chip default (on), contrary to
  the documentation. It now writes `automatic_self_calibration: false`.
- The web UI now shows the TMP119/SHT45 temperature cross-check, which was previously only
  logged. Cards for channels without a BACnet point no longer claim to be published.
