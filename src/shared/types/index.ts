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
