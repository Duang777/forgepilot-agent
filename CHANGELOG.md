# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),  
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- README first-screen optimization with bilingual navigation.
- Self-generated brand assets (`docs/assets/logo.svg`, `docs/assets/quick-demo.gif`).
- New documentation sections: `Versioning / Changelog`, `Security Policy`.
- Introduced `SECURITY.md` for responsible vulnerability disclosure.
- Runtime state backend abstraction (`sqlite | redis`) with fail-open/fail-closed behavior.
- Contract replay snapshots for `/agent`, `/agent/plan`, `/agent/execute` SSE streams.
- Release workflow (`.github/workflows/release.yml`) and changelog-driven notes generator (`scripts/generate_release_notes.py`).
- Environment templates: `.env.example` and `.env.production.example`.
- JWT auth mode (`jwt`, `api_key_or_jwt`) with configurable claims and HMAC algorithms.
- Route-level RBAC middleware with policy + subject-scope mapping.
- OpenTelemetry integration hooks for HTTP/SSE/agent execution spans.
- Redis permission decision event path (pub/sub) with polling fallback.
- Automated parity reporting pipeline (`scripts/generate_parity_report.py` + `docs/parity_report.md`).

### Changed
- Replaced third-party style homepage visuals with self-owned assets.
- Refined documentation structure to project-grade open-source layout.
- Production CORS defaults now auto-harden when `NODE_ENV=production`.
- Expanded observability metrics for SSE lifecycle, tool use/error, and sandbox fallback/provider distribution.
- Files ACL now accepts auth-scopes attached by API key/JWT authentication.
- CI now enforces strict baseline parity checks on each push/PR.

## [0.1.0] - 2026-04-08

### Added
- Initial Python-first runtime and API rewrite baseline.
- Agent plan/execute workflow and SSE streaming contracts.
- Provider integration (OpenAI-compatible / Anthropic).
- Storage, runtime state persistence, and security middleware stack.
- CI workflow, local verification pipeline, and desktop sidecar build chain.
