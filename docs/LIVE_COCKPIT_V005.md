# Forex-AI Live Trading Cockpit — v0.0.5

Operator scope: one desktop viewport (100dvh), with Balance, Equity, Floating PnL, UTC-day Realized Trading PnL, XAU/BTC broker markets, Quant/D3 dynamics, service state, and protected ARM/DISARM/KILL controls.

## Truth sources and freshness
- Financials: bound MT5 account_info through isolated read-only worker, refresh ~8 seconds and freshness timeout 25 seconds. If absent/stale, amounts are null and the UI displays —, never stale $0 from SQLite.
- Realized Trading PnL: broker history deals since 00:00 UTC, profit plus commission/swap/fees, filtering only nonempty symbol. Nontrade funding movements excluded.
- Tick stream: direct MT5 tick history for XAUUSDm and BTCUSDm, symbol-scoped timestamps and market health; only configured XAU symbol is eligible for trading. Monitoring BTC does not auto-enable the BTC strategy.
- Regime level 0–5 is a display quantization of fresh confirmed D3 confidence, NOT five independently tested confirmations. No recent D3 audit means no regime/confidence, displayed —.
- Bull/Bear [-1,1] = normalized signed tick movement; Momentum [0,1] = absolute signed tick efficiency; Volatility [0,1] = observed price range divided by 10 broker spreads (bounded). All are read-only diagnostics, NOT calibrated probability forecasts.
- D3 stays candle/candidate event-driven, never called per tick. No unsupported live confidence is animated or fabricated.

## Safety separation
- Legacy private strategy config missing only BTC squeeze now loads bundled defaults with BTC explicitly disabled. All other missing/unknown strategy IDs still fail.
- FX weekend entry cutoff remains for XAU; BTC is exempt from the FX calendar cutoff, but requires separate broker trade availability, fresh data, ARM, risk and D3 guards before a trade.
- FX weekend close excludes managed BTC positions. Existing XAU protection remains.
- Scanner service failure is distinguished from running timers and normally closed XAU market session.
- Details overlay contains graph and recent order journal; main page never scrolls vertically on desktop.
- Remote access remains owner-private SSH/Unix socket. No HTTP service exposed on LAN.

## Release gate
Deploy from exact remote-pushed annotated Git tag v0.0.5, after full Python regression, frontend integrity, actual DEMO broker binding, service/health smoke tests and preservation of ARM/KILL. Source commit SHA and timestamps are provenance only.
