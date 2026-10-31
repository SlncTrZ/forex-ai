export type GraphType = "strategy" | "symbol" | "candidate" | "risk" | "intent" | "execution";
export interface GraphNode {id:string;type:GraphType;label:string;[key:string]:unknown}
export interface GraphEdge {id:string;source:string;target:string;relation:string}
export interface GraphResponse {
  schema_version:number;source:string;generated_at:string;scope:{kind:string;limit:number;selected_candidates:number;total_candidates:number;risk_evaluated_in_sample:number;risk_approved_in_sample:number};
  nodes:GraphNode[];edges:GraphEdge[];
  summary:{visible_nodes:number;visible_edges:number;strategies:{id:string;count:number}[];visible_intents_by_state:Record<string,number>;visible_risk_approval_rate:number|null}
}
export interface Quote {id:number;type:string;symbol:string;bid:number;ask:number;spread:number;broker_time_ms:number;received_ms:number;age_at_receive_ms:number}
export interface StatusPayload {
 observed_at:string;infrastructure:string;
 account:{type:string;bound:boolean;age_seconds:number|null;financials:{balance:number|null;equity:number|null;currency:string}|null};
 control:{armed:boolean;kill_switch:boolean;execution_enabled:boolean;entries_allowed_by_control:boolean;reason:string;expiry:string|null};
 health:{healthy:boolean;heartbeat_age_seconds:number|null;db_integrity:string;unresolved_intents:number;reasons:string[]};
 metrics:{uncertain_orders:number;external_positions:number;audit_24h:number};
 ticks:{stream_state:string;last_poll_ms:number|null;last_received_ms:number|null;overflows:number;quotes:Quote[];symbols:string[]};
 services:{name:string;unit:string;status:string}[];release:{version:string}
}
export interface RegimeDecision {
 symbol:string;regime:"UPTREND"|"DOWNTREND"|"SIDEWAY"|"UNCERTAIN";
 quant_regime:string;model_regime:string|null;confidence:number|null;choice_probability?:number|null;
 market_state?:string;tick_age_seconds?:number|null;cache_hit?:boolean;confirmed_for_trading?:boolean;
 reason_code:string;closed_h1_utc:string|null;feature_fingerprint:string;
 model_fingerprint:string;latency_ms:number;age_seconds:number|null;
 observed_at:string;policy_version:string;
}
export interface RegimeResponse {source:"forex_ai_regime_audit";generated_at:string;regimes:RegimeDecision[]}
export interface ConfluenceDecision {
 candidate_id:string;strategy_id:string;symbol:string;regime:string;
 score:number;alignment_points:number;value_points:number;
 momentum_points:number;trigger_points:number;risk_multiplier:string;
 verdict:"FULL"|"REDUCE"|"VETO";reason_code:string;
 model_fingerprint:string;input_fingerprint:string;latency_ms:number;
 observed_at:string;policy_version:string;
}
export interface ConfluenceResponse {
 source:"forex_ai_confluence_audit";generated_at:string;
 decisions:ConfluenceDecision[];
}
export interface OperatorEvent {time:string;level:string;component:string;event:string;details:string}

export type MatrixState="BULL"|"BEAR"|"NEUTRAL"|"UNKNOWN"|"STALE"|"NA";
export interface MatrixCell {
 indicator:string;state:MatrixState;met:boolean;value:number|null;core:boolean;
 rule:string;closed_utc:string|null;
}
export interface MatrixStrategy {
 strategy_id:string;label:string;timeframe:"M5"|"M15";
 enabled:boolean;eligible:boolean;cells:MatrixCell[];
}
export interface SymbolMatrix {
 symbol:string;market_state:string;observed_at:string;
 age_seconds:number;diagnostic_only:true;rows:MatrixStrategy[];
}
export interface SignalMatrixResponse {
 source:"forex_ai_signal_matrix_audit";generated_at:string;policy_version:string;
 columns:{id:string;label:string;description:string}[];
 matrices:SymbolMatrix[];
}
