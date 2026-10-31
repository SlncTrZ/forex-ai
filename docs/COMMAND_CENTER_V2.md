# Forex-AI Command Center V2 — Phase 1 & 2

Status: isolated feature branch `feat/command-center-v2` — production deployment deferred to separate release gate.

## Architecture

```text
Operator browser (SSH-authenticated private socket)
  │ GET /                          GET /api/graph?limit=110
  ├── React/Vite static UI          ├── read-only SQLite journal
  ├── Sigma.js WebGL graph          ├── candidate_decisions
  ├── ECharts canvas telemetry      ├── risk_decisions_v1
  ├── SSE MT5 tickstream (existing) ├── order_intents_v1
  └── guarded Control API (existing)└── execution_transitions_v1
```

The graph represents **journal provenance**, not an inferred multi-agent knowledge
graph. A transition and a risk evaluation are never generated without persisted
records. The panel is not an execution decision engine. `schema_version=1`.

### Graph contract

`GET /api/graph?limit=N` (1 ≤ N ≤ 180, default 110) returns:

- `nodes`: `{id,type,label,...safe_metadata}`; types `strategy`, `symbol`,
  `candidate`, `risk`, `intent`, `execution`.
- `edges`: `{id,source,target,relation}`; relations `GENERATED`,
  `ON_SYMBOL`, `EVALUATED`, `CREATED_INTENT`, `TRANSITIONED`.
- `scope`: explicitly bounded newest-candidate sample and total historical
  candidate count.
- `summary`: visible nodes/edges, historical strategy candidate distribution,
  sample intent states and latest-risk approval **within the visible sample**.

The adapter performs bounded SQL reads, uses parameterized candidate IDs,
redacts rich payloads, and does not write to SQLite. Risk reasons are
short, allowlisted-string codes rather than raw broker payloads.
`403` applies to requests with an untrusted host, unchanged from the
original owner console.

### Frontend

React 19, TypeScript, Vite 7, Tailwind 3, Graphology/Sigma.js 3 WebGL and
ECharts 6 with selective imports. All frontend assets are self-hosted and
compatible with the original restrictive Content Security Policy.
The old standalone dashboard remains as a fallback when the compiled V2 UI is
absent. No public listener, browser credential or MT5 order API is introduced.

- Tick SSE continues to stream real broker quotes independently.
- Graph is reloaded at 18-second intervals, and Sigma is reconstructed only
  when node/edge identity changes.
- Operational API status remains on a 12-second cadence.
- Operator ARM/DISARM/KILL requests use the unchanged two-step confirmation and
  host/origin CSRF checks. GUI visibility does not bypass broker/risk admission.
- Baseline before/after benchmark improvement is explicitly shown as unavailable,
  until measured instrumentation is added.
- The historical Pareto counts are **signal frequency**, not strategy PnL.

### Release build integrity

On development/CI build host: `cd dashboard/ui && npm ci && npm run build`.
Build writes `dist/build-integrity.json` with a SHA256 of frontend source,
frontend configuration and the npm lockfile. Production deploy checks this
fingerprint **before** copying files and excludes `node_modules` from the
release. The systemd service runs only self-hosted static JS/CSS and Python.
Dependencies are not fetched on production activation.

### Verified checks

- New SQLite graph integration tests: source lineage, no invented risk stages,
  scope limits, read-only behavior and HTTP status.
- Existing trading-control / MT5 SSE tests remain passing.
- Desktop Chromium WebGL: 1530px, valid graph canvas, no page exceptions.
- Mobile Chromium: 390px, no horizontal overflow, no page exceptions.
- Manual review of a browser screenshot from the real read-only journal.
- Frontend TypeScript production build + fingerprint check.

### Boundaries for later phases

Graph agent/knowledge nodes require an independently verified CyberBrain
connector and provenance; do not fabricate them to match the graphic mockup.
Latency before/after comparison requires retained measurements with matching
methodology. 3D WebGL is optional, not part of the Phase-1/2 acceptance gate.
A production rollout needs its own release manifest, the five private control
documents, smoke checks and an explicit release/rollback plan.
