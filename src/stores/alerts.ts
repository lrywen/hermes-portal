import { defineStore } from 'pinia';
import { ref, markRaw } from 'vue';
import http from '@/shared/api/client';
import type { AlertConfig, AlertEvent } from '@/shared/types';
import { useVoiceBroadcast } from '@/shared/composables/useVoiceBroadcast';

// 实时提醒事件（从 SSE / 业务动作触发）
export interface AlertEventPayload {
  type: string; // 事件 code，如 position_opened / risk_alert
  title: string;
  content: string;
  level?: 'info' | 'success' | 'warn' | 'danger';
  ts?: number;
}

interface PopupItem extends AlertEventPayload {
  id: number;
  raw: unknown;
}

const LOG = '[AlertStore]';
const { broadcast } = useVoiceBroadcast();

export const useAlertStore = defineStore('alerts', () => {
  const config = ref<AlertConfig | null>(null);
  const eventCatalog = ref<AlertEvent[]>([]);
  const popups = ref<PopupItem[]>([]);
  let seq = 0;

  async function loadConfig() {
    console.log(LOG, 'loadConfig() — 拉取 /api/portal/alerts/config ...');
    const { data } = await http.get('/api/portal/alerts/config');
    config.value = data;
    console.log(
      LOG,
      'config 已加载 — voice_enabled:',
      data.voice_enabled,
      'voice_id:',
      data.voice_id,
      'voice_volume:',
      data.voice_volume,
      'popup_position:',
      data.popup_position,
      '订阅事件:',
      data.event_types
    );
  }

  async function loadEvents() {
    console.log(LOG, 'loadEvents() — 拉取 /api/portal/alerts/events ...');
    const { data } = await http.get('/api/portal/alerts/events');
    eventCatalog.value = data;
    console.log(LOG, '事件目录已加载，共', data.length, '个事件类型');
  }

  /**
   * 接收事件：若用户勾选了该事件类型，则弹窗 + 语音播报。
   */
  function emit(event: AlertEventPayload) {
    console.log(LOG, 'emit() — 收到事件:', {
      type: event.type,
      title: event.title,
      level: event.level ?? 'info',
    });

    if (!config.value) {
      console.warn(LOG, 'emit 跳过：config 尚未加载');
      return;
    }

    const enabled = config.value.event_types.includes(event.type);
    console.log(
      LOG,
      '事件类型匹配检查 —',
      event.type,
      enabled ? '✅ 已订阅，将弹窗+播报' : '⏭️ 未订阅，跳过'
    );
    if (!enabled) return;

    const id = ++seq;
    const item: PopupItem = {
      ...event,
      id,
      level: event.level || 'info',
      ts: event.ts || Date.now(),
      raw: markRaw(event),
    };
    popups.value.push(item);
    console.log(LOG, '弹窗已加入队列 — id:', id, '当前弹窗数:', popups.value.length, '位置:', config.value.popup_position);

    // 语音播报：
    //   event_voices[type] 为字符串 → 使用该自定义语音
    //   event_voices[type] === null → 该事件静默
    //   event_voices[type] 未定义 → 回退到全局 voice_id（null=系统 TTS）
    const perEvent = config.value.event_voices?.[event.type];
    const voiceId = perEvent === undefined ? config.value.voice_id : perEvent;
    // 只有 per-event 显式设为 null 才表示该事件静音；
    // voiceId === null 且 perEvent !== null 时表示使用系统 TTS（应播放）
    const isMuted = perEvent === null;
    const broadcastOpts = {
      voiceId,
      volume: config.value.voice_volume,
      enabled: config.value.voice_enabled && !isMuted,
    };
    console.log(LOG, '调用 broadcast() — 参数:', broadcastOpts);
    broadcast(`${event.title}。${event.content}`, broadcastOpts);

    // danger 级别需手动关闭，其余 8 秒自动消失
    if (event.level !== 'danger') {
      console.log(LOG, '非 danger 级别，8 秒后自动 dismiss — id:', id);
      setTimeout(() => dismiss(id), 8000);
    } else {
      console.log(LOG, 'danger 级别，需手动关闭 — id:', id);
    }
  }

  function dismiss(id: number) {
    const before = popups.value.length;
    popups.value = popups.value.filter((p) => p.id !== id);
    console.log(LOG, 'dismiss() — id:', id, '弹窗数:', before, '->', popups.value.length);
  }

  return { config, eventCatalog, popups, loadConfig, loadEvents, emit, dismiss };
});
