# Security policy

## Deployment model

HOOT is designed to sit on an **isolated building-automation network**. Keep that in mind
when you deploy it:

- **The web UI has no authentication by default** and listens on all interfaces. Anyone who
  can reach port 8080 can change the configuration and restart the service. On any network
  that is not a dedicated BAS VLAN, set `web.auth_enabled: true` and supply a password via
  `HOOT_WEB_PASSWORD` (see `deploy/hoot.service` for the `EnvironmentFile` pattern).
- HTTP Basic authentication is sent in clear text. If the UI must be reachable from a
  shared network, put it behind a TLS-terminating reverse proxy.
- **BACnet/IP has no authentication.** HOOT publishes read-only analog-input objects and
  accepts no writes to its readings, but any host on the BACnet network can read them.
- Anyone who can reach the web UI can also download the service log, event log,
  configuration and support bundle. None contain passwords (those live only in
  environment variables), but they do reveal addresses, device names and locations.
- `/api/health` is deliberately unauthenticated so monitoring systems can poll it. It
  reveals the device name, uptime, and faulted channel names.

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository, with steps to reproduce and the version (`hoot --version`, or
`device.version` in `/api/status`). We aim to acknowledge reports within a week.
