// 全局共享类型定义

export interface User {
  id: string;
  username: string;
  display_name: string;
  roles: string[];
  permissions: string[];
  is_active: boolean;
}

export interface MenuItem {
  id: string;
  label: string;
  path?: string;
  icon?: string;
  embedded?: boolean;
  children?: MenuItem[];
}

export interface PushChannel {
  id: string;
  channel: string;
  channel_type: string;
  enabled: boolean;
  name: string;
  config: Record<string, any>;
  updated_at?: string;
}

export interface AlertEvent {
  code: string;
  name: string;
  category: string;
  voice: boolean;
}

export interface AlertConfig {
  popup_position: 'bottom-right' | 'center' | 'top-right' | 'bottom-left';
  voice_enabled: boolean;
  voice_id: string | null;
  voice_volume: number;
  event_types: string[];
  event_voices: Record<string, string | null>;
  updated_at?: string;
}

export interface CustomVoice {
  id: string | null;
  name: string;
  filename?: string;
  content_type?: string;
  size_bytes?: number;
  is_default?: boolean;
  created_at?: string;
}

export interface AuditLog {
  id: string;
  actor_username: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  summary: string;
  before: any;
  after: any;
  ip: string | null;
  created_at: string;
}

export interface ToastMessage {
  id: number;
  kind: 'ok' | 'err' | 'info';
  text: string;
}

// ===== 交易核心数据（对应 hermes-trader /api/dashboard/* 载荷）=====

/** /api/dashboard/summary */
export interface TraderSummary {
  equity: number;
  available: number;
  dex_equity: Record<string, number>;
  dex_available: Record<string, number>;
  spot_usdc: number;
  daily_pnl: number;
  daily_pnl_pct: number;
  open_positions: number;
  last_tick_age_s: number | null;
  last_scan_triggers: number;
  status: string;
  ts: number;
}

/** DSL 移动止盈追踪器片段（持仓行内的 dsl 字段） */
export interface DslTrackerInfo {
  peak_px: number;
  floor_px: number | null;
  phase: 'phase1' | 'phase2';
}

/** /api/dashboard/positions 数组元素 */
export interface TraderPosition {
  coin: string;
  side: 'long' | 'short';
  size: number;
  leverage: number;
  entry_px: number;
  mark_px: number;
  unrealized_pnl_usd: number;
  unrealized_pct: number;
  spot_pct: number;
  dsl: DslTrackerInfo | null;
  liq_px: number | null;
}

/** 生效保护臂单项（risk-status.protection_arms 值） */
export interface ProtectionArm {
  enabled: boolean;
  mode: string;
  cap_pct?: number;
}

/** /api/dashboard/risk-status */
export interface TraderRiskStatus {
  global_halt: boolean;
  global_halt_remaining_min: number;
  coin_circuits: Record<string, unknown>;
  armed_coins: number;
  drawdown: DrawdownSnapshot | null;
  mode: string | null;
  daily_pnl: number | null;
  daily_loss_limit: number | null;
  kill_armed: boolean;
  open_positions: number;
  feed_status: 'live' | 'stale' | 'offline';
  feed_age_s: number | null;
  risk_blind: boolean;
  blind_gates: string[];
  market_circuit: Record<string, unknown>;
  protection_arms: Partial<Record<'sizing_v2' | 'trend_filter' | 'daily_extension_cap', ProtectionArm>>;
  ts: number;
}

/** /api/dashboard/config-parity */
export interface ConfigParityItem {
  leaf: string;
  canonical: unknown;
  live: unknown;
}

export interface ConfigParity {
  dangerous_count: number;
  items: ConfigParityItem[];
  ts: number;
}

/** risk-status.drawdown —— 滚动峰值/回撤冷却快照（memory.drawdown_snapshot） */
export interface DrawdownSnapshot {
  frozen: boolean;
  dd_pct: number;
  threshold_pct: number;
  peak_equity: number;
  all_time_peak_equity: number;
  equity: number;
  window_days: number;
  frozen_since_ms: number;
  frozen_for_min: number;
  cooldown_hours: number;
  cooldown_remaining_min: number;
  last_baseline_ms: number;
  trail_samples: number;
}
