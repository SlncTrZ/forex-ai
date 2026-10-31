# Forex-AI — Current Status

Last updated: 2026-10-31 10:30

## Runtime (v0.0.4)

- DEV source-of-truth: `<DEV_TREE>` (backed by Workstation `.171`)
- Compute & Backtest node: Ubuntu 26.04 (`192.168.1.152`), 56 vCPUs, 30 GB RAM, NVMe 151 GB
- Production runtime host: Ubuntu 24.04 (`192.168.1.227`), Docker `forex-mt5`, Exness Demo Trial7
- Current release: `$HOME/apps/forex-ai/current` -> `releases/20261030T161220Z-66d651f19029`
- Runtime venv: `$HOME/.venvs/forex-ai-runtime`
- SQLite: `$HOME/.local/share/forex-ai/forex.db`
- Observer service: `forex-ai-observe.service` active & healthy (78MB RAM, 0 leaks)
- Candidate scanner timer: `forex-ai-candidate-scan.timer` active, every 30 seconds
- Execution runner timer: `forex-ai-execute-pending.timer` active, every 10 seconds
- Reconcile timer: `forex-ai-execution-reconcile.timer` active, every 15 seconds
- Health watchdog: `forex-ai-ops-health.timer` active, every minute

## Standardized Backtest & Risk Invariants (2026-10-31)

Quy chuẩn bất biến bắt buộc cho toàn bộ các phiên làm việc của Agent ([docs/BACKTEST_PROTOCOL.md](docs/BACKTEST_PROTOCOL.md)):
1. **Nút Backtest độc quyền:** Mọi kiểm định backtest bắt buộc chạy trên dữ liệu nến thực tế MT5 tại Compute Node X99 (`192.168.1.152`). Nếu không thể truy cập `.152` (tắt nguồn, lỗi mạng, SSH), **dừng lại ngay và báo Anh xử lý**, không tự ý giả lập nến ảo.
2. **Vốn cơ sở chuẩn (Base Capital):** Cố định $V_0 = \mathbf{\$1,000\text{ USD}}$ (Level 0) cho mọi kịch bản backtest.
3. **Đồng nhất Quản lý rủi ro:** Luôn luôn áp dụng `TieredCompoundRiskEngine` (Level 0: 3.0% rủi ro, lot tính theo khoảng cách SL, de-escalation khi sụt giảm vốn, thăng/giáng cấp Level $+10\% / -10\%$) cho cả Backtest lẫn Live Trading.

## Account & Target Symbols

- Broker / Server: Exness / `Exness-MT5Trial7` (Demo) & `Exness-MT5Real36` (Real)
- Account currency: `USD` ($1,000 base capital V0 for backtest, live sync dynamic)
- Target Assets:
  - `XAUUSD -> XAUUSDm / XAUUSDc` (Gold, point = 0.001, min SL = 6.0$ / 600 pts)
  - `BTCUSD -> BTCUSDm / BTCUSDc` (Bitcoin 24/7, point = 0.01, min SL = 150.0$ / 15,000 pts)
  - `EURUSD -> EURUSDm / EURUSDc` (Euro FX, point = 0.00001, min SL = 0.0015 / 150 pts)
  - `USDJPY -> USDJPYm / USDJPYc` (Yen FX, point = 0.001, min SL = 0.15 / 150 pts)
- Sizing Mechanism: **Tiered Compound Risk Engine** (đã audit & khắc phục 2026-10-30)
  - V0 đồng bộ động theo High-Water Mark (HWM) hoặc vốn nạp ròng.
  - Tỷ lệ rủi ro có de-escalation thật sự khi drawdown ($L < 0 \implies 2\% \to 1\% \to 0.5\%$).
  - Chốt chặn bảo vệ `DRAWDOWN_LIMIT` 10% luôn có hiệu lực, không bị bypass bởi forced min lot.
  - Giới hạn rủi ro danh nghĩa được gông cứng bởi `max_risk_per_trade_pct`.

## Production Strategies (Top 4 Active Suite — Audit Findings 2026-10-30)

Danh mục 4 chiến lược nòng cốt chính thức được kích hoạt (`enabled: true`) trong cấu hình `config/strategy.yaml`:

1. `smc_pro_fvg_v2` (v2.0.0 — Quán quân Tăng trưởng Tiered Compound):
   - Đón lệnh Limit tại 50% FVG Consequent Encroachment sau Liquidity Sweep. Tận dụng Stop Loss siêu hẹp ($1.5 - $3.0) để đòn bẩy quy mô lot lớn theo Tiered Compound.
   - Thử nghiệm 12 tuần ngẫu nhiên 2026: Đạt tăng trưởng **+35.8% (+$3,579.09)**, đưa tài khoản chạm **Level 3 ($13,579.09)**.
2. `supertrend_volume_m5` (v2.0.0 — Bát Cơm M5 Bền Vững):
   - M5 Supertrend (10, 3.0) + H1 Trend Bias + Volume Confirmation.
   - Thử nghiệm 12 tuần ngẫu nhiên 2026: Tỷ lệ tuần xanh cao nhất hệ thống (**7/12 tuần dương, 58.3%**), PnL **+$730.15 (+7.3%)**, MaxDD tuần cực thấp chỉ $115.68.
3. `keylevel_breakout_v2` (v2.0.0 — Quán quân Alpha Khung Lớn M30):
   - Tự động nhận diện Keylevel Swing High/Low (15/5) trên khung M30 kết hợp H1 Trend Bias.
   - Thử nghiệm 12 tuần ngẫu nhiên 2026: Đạt **+$755.56 lợi nhuận ròng**, bùng nổ tại các tuần có trend lớn (Tuần 1 lãi +$857.34 với Win Rate 80.0%).
4. `supertrend_stochastic_m5` (v2.0.0 — Flagship Scalping Sweet Spot):
   - M5 Supertrend (10, 3.0) + Stochastic (7, 1, 3) Giao cắt vàng %K > %D $\le 30$ + H1 Trend Bias.
   - Kiểm định ma trận 100,000 nến: Đạt điểm ngọt **Win Rate 60.0% - 62.3%**, tỷ lệ RR 1:1.5, chuỗi 5 tuần thắng liên tiếp trong 2026, bảo toàn vốn tuyệt đối ở Level 0.

*Các chiến lược vệ tinh/thử nghiệm (`mtf_trend_continuation_v2`, `supertrend_pinbar_m5`, `supertrend_stoch_rsi_m5`, `supertrend_cci_m5`, `ema_ribbon_rsi_m5`, `pro_volatility_expansion_v2`, `ichimoku_mtf_v2`) được đặt ở trạng thái `enabled: false` để tập trung 100% ngân sách rủi ro cho Top 4.*

## Logging / audit guarantees

SQLite schema version: 5.

The system has an append-only `audit_events` black-box timeline with:

- `timestamp_utc` — ISO-8601 UTC with microseconds
- `epoch_ms` — system event time in milliseconds
- `market_time_msc` — authoritative MT5 market/deal timestamp when available
- `correlation_id` — links signal -> LLM -> risk -> execution timeline
- `event_type`, `source`, `symbol`, `entity_id`, full JSON payload

Event types prepared/used include:

- `SIGNAL`
- `LLM_CONTEXT`
- `LLM_WEB_TRACE`
- `LLM_DECISION`
- `LLM_ERROR`
- `ENTRY_REQUEST`
- `ENTRY_FILLED`
- `ENTRY_REJECTED`
- `EXIT_REQUEST`
- `EXIT_FILLED`
- `EXIT_REJECTED`
- `SL_TP_UPDATE`
- `RISK_REJECT`

MT5 deal reconciliation converts real trade fills into entry/exit audit events and stores both native `time_msc` and derived UTC fill time. Funding/balance deals are intentionally excluded from entry/exit audit.

A direct database verification recorded a sample LLM audit timestamp with microsecond precision:

`2026-09-03T04:21:59.174541+00:00`

Automated tests verify signal and LLM decision correlation plus precise audit timestamps.

## Live signal bot

The read-only candidate scanner runs every 30 seconds and reads live MT5 context for all three target symbols.

Signal generation uses only closed candles for technical evidence and currently considers:

- M5 / M15 / H1 / H4 trend state
- EMA20 / EMA50
- RSI14
- ATR14
- ADX14
- prior 20-bar high/low
- 20-bar breakout state
- distance from EMA20 in ATR units
- 20 recent closed candles per timeframe
- current tick only for a proposed entry reference

Initial strategies:

- `trend_breakout`
- `trend_pullback`
- `trend_continuation`

Signals need score >= 0.65. Duplicate signals on the same closed M15 candle/strategy/direction are prevented with a deterministic `signal_key`.

Current live scan has produced no valid signal yet; the bot does not manufacture trades just to generate activity.

## Full LLM context

Every DeepSeek review is built immediately from live state and includes:

- authoritative UTC clock with microseconds and epoch milliseconds
- Asia/Ho_Chi_Minh local time
- day/date and approximate active market sessions
- live account balance/equity/margin/free margin/leverage
- cent-account USD-equivalent values for readability
- all currently open positions across the account
- positions for the reviewed symbol
- current bid/ask/spread and tick timestamp/age
- broker contract/tick/volume constraints
- M5/M15/H1/H4 closed-candle technical context
- current still-forming candle separated from closed-candle evidence
- candidate signal details: strategy, direction, score, proposed entry/SL/TP/RR and evidence
- relevant historical lessons from SQLite
- symbol-specific current macro drivers that must be verified

The prompt explicitly tells the model that injected clock + MT5 state are authoritative and that training-time knowledge must not be treated as current market truth.

## DeepSeek / LLM Advisory State

- **Trạng thái:** **DISABLED (ĐÃ VÔ HIỆU HÓA HOÀN TOÀN)**
- **Lý do kiến trúc:** Tuân thủ Tôn chỉ Cốt lõi số 1 của Hiến pháp Kiến trúc (`00_FOREX_AI_MASTER_ARCHITECTURE.md`) — Chống Over-Engineering và triệt tiêu độ trễ mạng. Hệ thống vận hành như một **Lean Trading Engine định lượng thuần túy (100% Deterministic Quantitative)**.
- `config/llm.yaml`: `enabled: false`.
- `forex-ai-review-pending.timer`: Đã tắt và gỡ bỏ hoàn toàn khỏi systemd trên `.227`.
- Toàn bộ luồng thực thi đi trực tiếp:
  `Tín hiệu Chiến lược V2 -> Tường lửa Rủi ro & Lãi kép (Tiered Compound) -> MQL5 Execution Adapter`
  với độ trễ xử lý $< 15\text{ms}$, loại bỏ hoàn toàn rủi ro hallucination, chi phí API token và độ trễ chờ đợi mạng 30s của LLM.

## Safety & Execution State

- Runtime mode: `DEMO` (Campaign: `demo_month_continuous`)
- `execution_enabled: true` in production `~/.config/forex-ai/risk.yaml`
- Execution arm state: `armed: true`, `arm_expires_at_utc: 2026-11-28`, `kill_switch: false`
- `BrokerAwareRiskEngine` integrated with `TieredCompoundRiskEngine`:
  - `enable_tiered_compound: true`
  - `initial_deposit_v0: 10000`
  - `allow_forced_min_lot: true`
  - `max_active_orders: 3`, `max_total_open_risk_pct: 9%`
- Reconcile engine classifies manual trader interventions as `MANUAL_CLOSE` and updates equity in real-time.
- Unbound or foreign positions are tagged as `ORPHAN_BROKER_POSITION` and block conflicting automated orders.

## Tests

Current remediation working tree: **271 passing tests** on Linux `.152` / **257 passing tests** on Windows `.171` (`pytest -q`).

Key test suites:
- `tests/test_tiered_risk_integration.py` — Tiered Compound Level calculation & dynamic volume sizing
- `tests/test_execution_lifecycle_close.py` — Manual close, broker reconciliation, and reason classification
- `tests/test_strategy_v2.py` — Strategy V2 institutional logic (Supertrend, SMC FVG, MTF Continuation)
- `tests/agent_a/test_kernel.py` & `tests/agent_b/` — Kernel and risk invariants
- `tests/test_auto_week.py` — Live approval and schedule constraints

## Current Milestone (Completed v0.0.4)

1. [x] Implement Tiered Compound Risk Engine from `_private/lãi kép.xlsx` with Level 0-100+ de-escalation.
2. [x] Implement 3 institutional V2 strategies (`supertrend_volume_m5`, `smc_pro_fvg_v2`, `mtf_trend_continuation_v2`).
3. [x] Complete 2-year multi-asset backtest on node `.152` across 4 symbols (XAUUSD, BTCUSD, EURUSD, USDJPY).
4. [x] Complete 3-month segmented empirical audit (August, September, October 2026) demonstrating steady compounding.
5. [x] Scale `max_active_orders: 3` and remove fixed volume constraints on production node `.227`.
6. [x] Stand by for next operator command.
