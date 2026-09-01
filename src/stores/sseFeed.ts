/**
 * 全局 SSE 实时事件流（单例）。
 *
 * 原先 EventSource 只在 Channels.vue 挂载时连接，导致用户停留在其他页面
 * （如提醒设置、仪表盘）时无法接收实时事件，全局弹窗/提示音也就无法触发。
 * 此 store 在登录后由 AppShell 启动，整个会话期间保持单条连接；
 * Channels 页面通过 onEvent() 订阅即可，不再各自建立连接。
 */
import { defineStore } from 'pinia';
import { ref } from 'vue';
import http from '@/shared/api/client';
import { useAuthStore } from '@/stores/auth';
import { mapTraderEvent } from '@/shared/composables/useTraderEventBridge';
import { useAlertStore } from '@/stores/alerts';

type Unsub = () => void;

const LOG = '[SseFeed]';

export const useSseFeedStore = defineStore('sseFeed', () => {
  const auth = useAuthStore();

  const connected = ref(false);
  const reconnecting = ref(false);
  const lastMessageTs = ref(0);
  // 诊断信息（直接显示在页面上，无需 F12）
  const lastEvent = ref<{ event: string; ts: number; age: number; raw: string } | null>(null);
  const lastBridge = ref<{ event: string; age: number; action: string; mapped?: string } | null>(null);
  const eventCount = ref(0);

  // scan 心跳提示音开关（localStorage 持久化，默认关闭）
  const HB_SOUND_KEY = 'hermes.scanHeartbeatSound';
  const heartbeatSoundEnabled = ref(localStorage.getItem(HB_SOUND_KEY) === '1');
  function setHeartbeatSound(v: boolean) {
    heartbeatSoundEnabled.value = v;
    localStorage.setItem(HB_SOUND_KEY, v ? '1' : '0');
  }

  // 用 Web Audio API 生成短促"滴"声，无需音频文件
  let audioCtx: AudioContext | null = null;
  function playBeep() {
    try {
      if (!audioCtx) {
        const AC = window.AudioContext || (window as any).webkitAudioContext;
        if (!AC) return;
        audioCtx = new AC();
      }
      if (audioCtx.state === 'suspended') void audioCtx.resume();
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = 'sine';
      osc.frequency.value = 880; // A5
      gain.gain.setValueAtTime(0.0001, audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.15, audioCtx.currentTime + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.12);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start();
      osc.stop(audioCtx.currentTime + 0.13);
    } catch (e) {
      console.warn(LOG, '播放心跳提示音失败：', e);
    }
  }

  let es: EventSource | null = null;
  let reconnectTimer: number | null = null;
  let heartbeatTimer: number | null = null;
  let refreshAttempted = false;
  let reconnectAttempts = 0;
  let started = false;
  // 连接建立时间戳：用于跳过连接初期回放的历史事件。
  // trader SSE 会在连接时回放最近 500 条；这些事件不应弹窗。
  // 注意：不能用 age >= 0 判断新鲜度——服务器时钟可能比浏览器快几秒，
  // 导致实时事件的 age 为负数而被误杀。改为：连接后短暂跳过回放，
  // 之后只过滤"太久以前(age>=10s)"的事件，允许负年龄（时钟漂移的实时事件）。
  let connectedAt = 0;

  // 事件订阅者（Channels 列表、alert 桥接等）
  const listeners = new Set<(data: any) => void>();

  function emit(data: any) {
    for (const fn of listeners) {
      try {
        fn(data);
      } catch (e) {
        console.warn('[SseFeed] listener error:', e);
      }
    }
  }

  function onEvent(fn: (data: any) => void): Unsub {
    listeners.add(fn);
    return () => listeners.delete(fn);
  }

  function teardown() {
    stopHeartbeat();
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (es) {
      es.onopen = null;
      es.onmessage = null;
      es.onerror = null;
      es.close();
      es = null;
    }
  }

  function scheduleReconnect() {
    connected.value = false;
    if (reconnectTimer !== null) return;
    reconnecting.value = true;
    // 指数退避：1s → 2s → 4s … 上限 30s
    const delayMs = Math.min(1000 * 2 ** reconnectAttempts, 30000);
    reconnectAttempts++;
    reconnectTimer = window.setTimeout(async () => {
      reconnectTimer = null;
      await connect();
    }, delayMs);
  }

  function startHeartbeat() {
    stopHeartbeat();
    lastMessageTs.value = Date.now();
    // 每 5 秒检查：超过 45 秒无任何消息则判定僵死并强制重连
    heartbeatTimer = window.setInterval(() => {
      if (Date.now() - lastMessageTs.value > 45000) {
        stopHeartbeat();
        teardown();
        scheduleReconnect();
      }
    }, 5000);
  }

  function stopHeartbeat() {
    if (heartbeatTimer !== null) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  }

  async function connect() {
    teardown();
    if (!auth.accessToken) {
      // 无内存 access token：若仍有 refresh Cookie 可先静默换新再重连
      if (!refreshAttempted && auth.canRefresh) {
        refreshAttempted = true;
        const newToken = await auth.refresh();
        if (newToken) {
          await connect();
          return;
        }
      }
      scheduleReconnect();
      return;
    }
    // EventSource 不能设置 Header，改用 60s 短时 SSE 票据（不再把 15 分钟
    // access token 拼进 URL，避免其落入 nginx/BFF/上游访问日志）。
    // 每次连接（含重连）都重新取票，票据过期即随重连自动刷新。
    let ticket = '';
    try {
      const resp = await http.get('/api/portal/auth/sse-ticket');
      ticket = resp.data?.ticket || '';
    } catch (e) {
      console.warn(LOG, '获取 SSE 票据失败，将重连重试:', e);
      scheduleReconnect();
      return;
    }
    if (!ticket) {
      scheduleReconnect();
      return;
    }
    const url = `/api/portal/trader/api/feed/stream?ticket=${encodeURIComponent(ticket)}`;
    console.log(LOG, 'connect() — 建立 EventSource 连接（短时票据）');
    es = new EventSource(url);
    es.onopen = () => {
      connected.value = true;
      reconnecting.value = false;
      refreshAttempted = false;
      reconnectAttempts = 0;
      connectedAt = Date.now();
      startHeartbeat();
      console.log(LOG, '✅ SSE 连接已建立 — connected=true（回放窗口期 1.5s 内不弹窗）');
    };
    es.onmessage = (e) => {
      lastMessageTs.value = Date.now();
      try {
        const data = JSON.parse(e.data);
        if (!data.ts && !data.timestamp) data.ts = Date.now();
        const evType = data.event ?? '(无 event 字段)';
        const age = Date.now() - (data.ts || Date.now());
        eventCount.value++;
        lastEvent.value = {
          event: evType,
          ts: data.ts || Date.now(),
          age,
          raw: e.data.length > 300 ? e.data.slice(0, 300) + '…' : e.data,
        };
        console.log(LOG, '📨 收到事件 — event:', evType, 'age:', age + 'ms', '订阅者数:', listeners.size, data);
        // scan 心跳提示音：仅实时事件（非回放期、非陈旧历史），且开关打开时响一声
        if (
          evType === 'scan' &&
          heartbeatSoundEnabled.value &&
          connectedAt > 0 &&
          Date.now() - connectedAt >= 1500 &&
          age < 10_000
        ) {
          playBeep();
        }
        emit(data);
      } catch (err) {
        console.warn(LOG, 'JSON.parse 失败，作为 raw 事件派发:', e.data, err);
        emit({ event: 'raw', message: e.data, ts: Date.now() });
      }
    };
    es.onerror = async () => {
      console.warn(LOG, '❌ SSE onerror — connected=false, 准备重连 (refreshAttempted=' + refreshAttempted + ')');
      connected.value = false;
      teardown();
      // EventSource 读不到 HTTP 状态码；access token TTL 15 分钟，过期后取票
      // 会 401。首次错误先刷新 access token 再重连（重连会重新取短时票据），
      // 避免用过期会话循环 401。
      if (!refreshAttempted && auth.canRefresh) {
        refreshAttempted = true;
        const newToken = await auth.refresh();
        if (newToken) {
          await connect();
          return;
        }
      }
      scheduleReconnect();
    };
  }

  /** AppShell 登录后调用一次，幂等。 */
  function start() {
    if (started) {
      console.log(LOG, 'start() 已调用过，跳过（幂等）');
      return;
    }
    started = true;
    console.log(LOG, 'start() — 启动全局 SSE 单例，注册 alert 桥接监听器');
    bindAutoReconnect();
    // 全局提醒桥接：实时（10s 内）trader 事件 → alert 弹窗/语音。
    // 历史回放（>10s）不弹窗，避免打开页面时弹出一堆旧提醒。
    onEvent((data) => {
      const ts = data.ts || data.timestamp || Date.now();
      const age = Date.now() - ts;
      const evType = data.event ?? '(raw)';
      // 新鲜度判断：
      //  1) 连接建立后 1.5s 内收到的一律视为历史回放，不弹窗（trader 会回放最近 500 条）；
      //  2) 1.5s 后，事件时间戳不早于现在 10s 即视为实时（age 可为负，容忍服务器时钟超前）。
      const inReplayWindow = connectedAt > 0 && Date.now() - connectedAt < 1500;
      const isFresh = !inReplayWindow && age < 10_000;
      let action = '';
      let mapped = '';
      if (inReplayWindow) {
        action = '连接回放期，跳过弹窗';
      } else if (!isFresh) {
        action = `历史事件(age=${(age / 1000).toFixed(1)}s)，跳过弹窗`;
      } else {
        const m = mapTraderEvent(data);
        if (!m) {
          action = '无需弹窗（类型忽略）';
        } else {
          action = '派发 alert';
          mapped = `${m.type}/${m.level}`;
          try {
            useAlertStore().emit({
              type: m.type,
              level: m.level,
              title: m.title,
              content: m.content,
              ts: data.ts || data.timestamp || Date.now(),
            });
          } catch (e) {
            console.warn(LOG, 'emit 失败：', e);
          }
        }
      }
      lastBridge.value = { event: evType, age, action, mapped };
      console.log(
        LOG,
        '🔀 桥接过滤 — event:',
        evType,
        'age:',
        age + 'ms',
        '→',
        action
      );
    });
    connect();
  }

  /** 手动重连（Channels 页面按钮用）。 */
  async function manualReconnect() {
    refreshAttempted = false;
    reconnectAttempts = 0;
    await connect();
  }

  /**
   * 页面从后台切回前台时调用：若连接已断或长时间无消息，立即重连。
   * 解决容器重建/网络切换后 TCP 半开、需等 45s 心跳才发现的问题。
   */
  function ensureConnected() {
    if (!started) return;
    const stale = Date.now() - lastMessageTs.value > 15000;
    if (!connected.value || reconnecting.value || stale) {
      console.log(LOG, '🔄 ensureConnected() — 连接异常或过期，立即重连 (connected=' + connected.value + ', stale=' + stale + ')');
      teardown();
      void connect();
    }
  }

  /** 绑定可见性事件：切回标签页时确保连接存活。 */
  function bindAutoReconnect() {
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) ensureConnected();
    });
    window.addEventListener('online', () => ensureConnected());
  }

  function stop() {
    started = false;
    teardown();
    listeners.clear();
  }

  return {
    connected,
    reconnecting,
    lastMessageTs,
    lastEvent,
    lastBridge,
    eventCount,
    heartbeatSoundEnabled,
    setHeartbeatSound,
    playBeep,
    start,
    stop,
    connect,
    manualReconnect,
    ensureConnected,
    onEvent,
  };
});
