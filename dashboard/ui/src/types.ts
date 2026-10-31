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
 services:{name:string;status:string}[];release:{version:string}
}
export interface OperatorEvent {time:string;level:string;component:string;event:string;details:string}
