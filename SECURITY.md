# Security Policy

## Reporting a vulnerability

**Please do not open a public issue, PR or discussion for security problems.**

Report privately through GitHub:
**Security → Report a vulnerability** on this repository
(<https://github.com/Wildstone-Systems/wildstone-pantry/security/advisories/new>).

Include what you found, how to reproduce it, and the version or commit.
You should get an acknowledgement within 7 days. Please give us a reasonable
window to ship a fix before disclosing publicly.

## Supported versions

Only the latest tagged release (and `main`) receives security fixes.

## Scope notes

- PantryOS is designed to run on a trusted home network. Exposing it directly
  to the internet is not a supported configuration.
- Never post real credentials, `.env` files, IP addresses or hostnames from
  your own install in issues. Use placeholders such as `<household-host>`.
