# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| 0.1.x | :white_check_mark: |
| < 0.1.0 | :x: |

## Reporting a Vulnerability

Please do **not** disclose exploitable details in public issues.

### Preferred process

1. Send a private report to the project maintainer.
2. Include reproduction steps, impact scope, and environment details.
3. If possible, include a minimal PoC and suggested mitigation.

### Response expectations

- Acknowledgement: within **72 hours**.
- Initial triage: within **7 days**.
- Fix timeline: based on severity and exploitability.

## Disclosure Guidelines

- Use coordinated disclosure.
- Avoid publishing zero-day details before a fix is available.
- Credit security researchers in release notes when appropriate.

## Recommended Production Baseline

- Enable API authentication (`FORGEPILOT_AUTH_MODE=api_key`).
- Enable request rate limiting.
- Keep audit logging enabled.
- Use `FORGEPILOT_FILES_MODE=prod` and restrictive `/files` ACL scopes.
- Rotate API keys and review audit logs regularly.
