# Forex-AI Command Center V2 · Development and Build Contract

This React frontend is a **presentation layer** for the existing Python operations service. The backend continues to own broker access, risk, journal, ARM/DISARM/KILL and the owner-only Unix socket.

## Build a local release candidate

```bash
cd dashboard/ui
npm ci
npm run build
```

The build command checks TypeScript, bundles with Vite and writes `dist/build-integrity.json` containing a deterministic SHA-256 fingerprint over the checked-in frontend source and npm lockfile. `deploy_local.sh` rejects missing/stale frontend builds; it excludes `node_modules` and copies only static assets. The browser never receives broker secrets or a new authentication token.

## API contract

- `GET /api/graph?limit=110`: versioned, bounded, journal-derived execution graph. Nodes have `id,type,label`; edges have `id,source,target,relation`. The server reads SQLite in read-only mode and **does not fabricate absent risk or execution stages**.
- `GET /api/status`: operational status, account snapshot, safety and services.
- `GET /api/orders`: recent execution order intents and external positions.
- `GET /api/events`: sanitized audit/system events.
- `GET /api/stream/ticks`: existing SSE market feed with reconnect/gap semantics.
- `GET /api/preflight`, `POST /api/control/*`: unchanged safety-confirmed operator actions.

The Graph sample risk approval rate is **not** overall strategy profitability. The benchmark panel intentionally marks before/after improvements unavailable until those baselines are instrumented. There is no production mock mode.

## Development and QA

```bash
npm run dev
npm run build
```

The built frontend is served as static files by the existing Python Unix-socket service. `scripts/visual-qa.mjs` can inspect a local, authenticated owner preview when `DASHBOARD_QA_URL` points at its loopback SSH-forwarded URL. It relies on Chromium and the dev-only `playwright-core` package. No public TCP operator listener is required.

See `docs/COMMAND_CENTER_V2.md` for graph semantics, deployment gates, accessibility and known limitations.
