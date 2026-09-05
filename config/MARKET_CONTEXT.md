# Market context configuration

`config/market-context.yaml` defines descriptive market context that strategies may inspect as evidence. It is deliberately separate from `strategy.yaml` because these settings do not define trade permission or risk.

Runtime uses a persistent copy at:

`~/.config/forex-ai/market-context.yaml`

or the path specified by `FOREX_AI_MARKET_CONTEXT_CONFIG`.

Deployment seeds the persistent file only when absent and never overwrites later edits. The scanner reloads and validates this YAML on every invocation, so edits take effect without restarting services. Invalid edits fall back to `market-context.last-good.yaml` and are journaled as `MARKET_CONTEXT_CONFIG_RELOAD_REJECTED`.

## Higher-timeframe structure boundary

H4 and D1 are scanned only to derive support/resistance context. The context path is intentionally non-blocking:

- H4/D1 refresh failure must not make broker sync unhealthy;
- risk/execution readiness does not depend on these levels;
- context is stored in `MarketSnapshot.context`, which is excluded from `decision_fingerprint`;
- changing a support/resistance context snapshot cannot create a different candidate id by itself;
- legacy strategy timeframes remain separate from this context cache.

The default refresh interval is 300 seconds. H4 and D1 bars are fetched together for the full symbol universe in one bars-only bridge round-trip.

## Level extraction

For each timeframe the extractor uses only closed bars and configurable values for:

- history length;
- ATR period;
- left/right pivot confirmation bars;
- ATR-normalized clustering distance;
- ATR-normalized zone half-width;
- timeframe importance weight.

Confirmed pivot highs/lows and currently-visible range extrema are clustered into price zones. A zone below current price is labeled current `support`; a zone above current price is labeled current `resistance`. The original pivot roles remain recorded in `origins`.

D1 receives a larger default importance weight than H4, but this is only ranking context, not a trade signal.

## Scalping use

Future M5/M15 scalping strategies should consume only the derived support/resistance zone fields when they need higher-timeframe context. They should not require H4/D1 trend alignment. H1 may remain a weak/context feature depending on the strategy definition.
