# Compatibility Matrix (Node -> Python)

## API routes

| Route | Node behavior | Python status |
|---|---|---|
| `POST /agent` | plan+execute legacy stream | Implemented |
| `POST /agent/plan` | planning stream | Implemented |
| `POST /agent/execute` | execute approved plan stream | Implemented |
| `POST /agent/chat` | lightweight chat stream | Implemented |
| `POST /agent/title` | generate short title | Implemented |
| `POST /agent/stop/{sessionId}` | stop running session | Implemented |
| `GET /agent/session/{sessionId}` | session status | Implemented |
| `GET /agent/plan/{planId}` | read plan | Implemented |
| `GET /health` | health check | Implemented |
| `GET /health/dependencies*` | dependencies compatibility endpoints | Implemented |
| `POST /sandbox/exec` | run command in provider | Implemented (native compatibility mode) |
| `POST /sandbox/run/file` | run script | Implemented (native compatibility mode) |
| `POST /sandbox/run/node` | run node script content | Implemented |
| `POST /sandbox/run/python` | run python script content | Implemented |
| `POST /sandbox/exec/stream` | stream command output via SSE | Implemented |
| `POST /sandbox/stop-all` | stop all sandbox providers | Implemented (compatibility no-op) |
| `GET /sandbox/debug/codex-paths` | debug endpoint | Implemented (compatibility stub) |
| `GET /sandbox/available` | provider capability | Implemented |
| `GET /sandbox/images` | image list | Implemented |
| `GET /sandbox/pool/stats` | pool observability (internal parity support) | Implemented |
| `GET /providers` | provider settings/capabilities | Implemented |
| `GET /files/task/{taskId}` | files list | Implemented |
| `GET /mcp` | mcp config listing | Implemented |
| `POST /mcp/load` | load custom mcp config path | Implemented |

## SSE event contract

Supported event types:

- `text`
- `tool_use`
- `tool_result`
- `result`
- `error`
- `session`
- `done`
- `plan`
- `direct_answer`

Framing:

- `data: <json>\n\n`

Planning/execution behavior:

- Planning phase now follows the SDK query loop with no-tool configuration and robust response parsing.
- Supports both strict JSON output and noisy mixed output by extracting JSON objects/fallback text.
- Execution phase consumes approved plan content and deletes plan cache after completion.
- Permission flow supports request/response roundtrip:
  - engine emits `permission_request` events for mutable tools in non-bypass mode
  - engine `system.init.permission_mode` reflects configured runtime mode
  - `/agent/permission` resolves pending requests by `sessionId + permissionId`
  - deny result is returned as tool error output
- Model config resolution supports Codex-local fallback (`~/.codex/config.toml` + `~/.codex/auth.json`) when request/provider API config is incomplete.

## Tool compatibility (full baseline family)

- Core: `Read` `Write` `Edit` `Glob` `Grep` `Bash` `NotebookEdit`
- Web: `WebSearch` `WebFetch`
- Agent/Team: `Agent` `SendMessage` `TeamCreate` `TeamDelete`
- Task: `TaskCreate` `TaskList` `TaskUpdate` `TaskGet` `TaskStop` `TaskOutput` + legacy `Task`
- Workflow: `EnterWorktree` `ExitWorktree` `EnterPlanMode` `ExitPlanMode` `AskUserQuestion` `ToolSearch`
- MCP resources: `ListMcpResources` `ReadMcpResource`
- Scheduling/config: `CronCreate` `CronDelete` `CronList` `RemoteTrigger` `Config` `TodoWrite`
- Code intelligence & skills: `LSP` `Skill`

## Storage contract

SQLite tables:

- `sessions`
- `tasks`
- `messages`
- `files`
- `settings`

Path contract:

- app dir: `~/.forgepilot`
- app data sessions dir: `~/.forgepilot/sessions` (task/session DB metadata)
- SDK transcript sessions dir: `~/.open-agent-sdk/sessions/<id>/transcript.json` (TypeScript-compatible)
- mcp config: `~/.forgepilot/mcp.json`
- skills dir: `~/.forgepilot/skills` and `~/.claude/skills`

Runtime persistence:

- Agent streaming events are persisted to SQLite (`sessions/tasks/messages`) when `taskId` is provided on `/agent` and `/agent/execute`.
- Task indexing/counting now uses session-level atomic reservation when creating new tasks (`task_index` monotonic within the same session, `sessions.task_count` synchronized).

Provider runtime behavior:

- Sandbox providers are resolved via registry and runtime availability checks (not static flags).
- Provider settings from `/providers/settings/sync` are persisted and used as default provider selection in `/sandbox/*`.
- Fallback behavior is explicit (`usedFallback`, `fallbackReason`) in sandbox responses.
- `/sandbox/*` missing-parameter and execution failures now follow upstream error contracts (`{ error }` for 4xx and detailed `success=false` payload for 500s).
- Optional sandbox pooling is supported with provider lease/release lifecycle in `/sandbox/exec`, `/sandbox/run/*`, and `/sandbox/exec/stream`.
  - `FORGEPILOT_SANDBOX_POOL_ENABLED=1` to enable
  - `FORGEPILOT_SANDBOX_POOL_MAX_SIZE=<n>` to tune pool size
- `/sandbox/run/file` supports network-package auto-detection and will force `native` provider when packages imply outbound networking and request does not explicitly pin a provider.
- Pool access path now uses lock-protected acquire/cleanup/stop lifecycle to avoid race conditions under concurrent requests.
- Provider bootstrap defaults to `codex` sandbox and can seed agent config/model from Codex local runtime config when frontend sync config is absent.

## Known gaps for phase-4 parity

- Advanced hook lifecycle parity with TypeScript upstream
- Long-lived provider lifecycle orchestration edge-cases under extreme concurrent load
- Provider-level multimodal input parity for non-text model payloads
