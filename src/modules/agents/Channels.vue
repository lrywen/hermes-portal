<script setup lang="ts">
/**
 * 渠道消息 / 实时事件流
 * - 通过 SSE /api/feed/stream（经 BFF 代理）实时接收智能体事件
 * - 支持事件类型筛选、关键字搜索、暂停/恢复、清空
 * - 左侧事件列表 + 右侧详情面板（字段中文化）
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import http from '@/shared/api/client';
import { useToast } from '@/stores/toast';
import { useSseFeedStore } from '@/stores/sseFeed';

const toast = useToast();
const sseFeed = useSseFeedStore();
// 连接状态直接来自全局 SSE store（storeToRefs 保持响应性）
const { connected, reconnecting } = storeToRefs(sseFeed);

const events = ref<any[]>([]);
const selectedTypes = ref<Set<string>>(new Set());
const search = ref('');
const paused = ref(false);
const selectedEvent = ref<any>(null);

// 扫描模式（币种名单：作用于开仓风控 gate，不缩小扫描范围）
const scanMode = ref<'all' | 'allowlist' | 'blocklist'>('all');
const coinAllowlist = ref<string[]>([]);
const coinBlocklist = ref<string[]>([]);
const scanModeSaving = ref(false);
const coinInput = ref('');

// HIP-3 场馆（DEX）名单：作用于扫描聚合层，被排除的场馆不扫描、不产生事件
const dexAllowlist = ref<string[]>([]);
const dexBlocklist = ref<string[]>([]);
const dexAllowInput = ref('');
const dexBlockInput = ref('');
const hip3Enabled = ref(false);

// 单页保留上限：历史接口最多拉 MAX_EVENTS 条，本地持久化最近 STORAGE_MAX 条。
// 渲染只挂载最近 RENDER_LIMIT 条到 DOM（滚动可继续加载），避免数千节点卡死主线程。
const MAX_EVENTS = 1000;
const STORAGE_KEY = 'hermes_channel_events';
const STORAGE_MAX = 1000;
const RENDER_LIMIT = 200;
const renderCount = ref(RENDER_LIMIT);

// 批量合并缓冲：SSE 单条事件到达时先入队，微任务批量 flush，避免每条都全量替换数组
let pendingBuffer: any[] = [];
let flushScheduled = false;
// (supplemental audit 2026-09-02) 已观测到的"实时新事件"最大时间戳（ms）。
// SSE 每次（重）连接会回放最近 500 条、history/localStorage 也会回填历史，
// 这些回放事件带的是几分钟前的原始 ts，不能前插到列表顶部伪装成"刚滚动进来"。
// 首次收到实时事件后才启用；早于该水位的到达事件一律按回填处理（时间归位）。
let liveSeenMaxTs = 0;
// 持久化防抖：3 秒内多次写入合并为一次，避免每条事件都同步序列化上千条
let persistTimer: number | null = null;
// 搜索文本缓存：避免每次搜索对每条事件 JSON.stringify
const searchHay = new WeakMap<object, string>();

const eventTypes = [
  { key: 'scan', label: '扫描' },
  { key: 'research', label: '研判' },
  { key: 'near_miss', label: '未达标' },
  { key: 'execute', label: '执行' },
  { key: 'execute-fail', label: '执行失败' },
  { key: 'ta_skip', label: 'TA跳过' },
  { key: 'dsl_exit', label: 'DSL退出' },
  { key: 'loop_heartbeat', label: '心跳' },
  { key: 'loop_start', label: '循环' },
  { key: 'error', label: '错误' },
] as const;

const EVENT_TYPE_LABELS: Record<string, string> = Object.fromEntries(
  eventTypes.map((t) => [t.key, t.label]),
);

const SCAN_MODES = [
  { key: 'all', label: '全局扫描', desc: '扫描所有市场' },
  { key: 'allowlist', label: '仅白名单', desc: '只扫描白名单币种' },
  { key: 'blocklist', label: '除黑名单外', desc: '排除黑名单币种' },
] as const;

const SCAN_MODE_LABELS: Record<string, string> = {
  all: '全局扫描',
  allowlist: '仅白名单',
  blocklist: '除黑名单外',
};

const EVENT_COLORS: Record<string, string> = {
  scan: 'text-cyan-300 bg-cyan-500/10 border-cyan-500/30',
  research: 'text-violet-300 bg-violet-500/10 border-violet-500/30',
  near_miss: 'text-orange-300 bg-orange-500/10 border-orange-500/30',
  execute: 'text-emerald-300 bg-emerald-500/10 border-emerald-500/30',
  'execute-fail': 'text-rose-300 bg-rose-500/10 border-rose-500/30',
  ta_skip: 'text-slate-400 bg-slate-500/10 border-slate-500/30',
  dsl_exit: 'text-amber-300 bg-amber-500/10 border-amber-500/30',
  loop_heartbeat: 'text-slate-500 bg-slate-500/5 border-slate-500/20',
  loop_start: 'text-sky-300 bg-sky-500/10 border-sky-500/30',
  error: 'text-rose-400 bg-rose-500/15 border-rose-500/30',
};

const FIELD_LABELS: Record<string, string> = {
  ts: '时间', event: '事件', coin: '币种', side: '方向', score: '评分',
  verdict: '裁决', reason: '原因', detail: '详情', triggers: '触发数',
  executed: '已执行', error: '错误', msg: '消息', message: '消息',
  px: '价格', qty: '数量', leverage: '杠杆', pnl: '盈亏', pnl_pct: '盈亏%',
  source: '来源', mode: '模式', interval: '间隔', count: '计数',
  entry_px: '开仓价', fill_px: '成交价', fees: '手续费', fees_pct: '手续费%',
  spot_pct: '现货占比', size: '仓位', mkt_price: '市场价', note: '备注',
  action: '动作', symbol: '标的', strategy: '策略', ts_ms: '时间戳',
  // (supplemental audit 2026-09-02) scan 事件补「扫描耗时」；start_ts_ms 为
  // 扫描开始时刻(epoch ms)，已用于时间列显示，详情里不重复原始数字故跳过。
  scan_duration_ms: '扫描耗时',
};

const SIDE_LABELS: Record<string, string> = { long: '做多', short: '做空' };

const counts = computed(() => {
  const m = new Map<string, number>();
  for (const e of events.value) {
    const k = e.event || 'unknown';
    m.set(k, (m.get(k) || 0) + 1);
  }
  return m;
});

const filtered = computed(() => {
  let list = events.value;
  if (selectedTypes.value.size > 0) {
    list = list.filter((e) => selectedTypes.value.has(e.event));
  }
  const q = search.value.trim().toLowerCase();
  if (q) {
    list = list.filter((e) => {
      let hay = searchHay.get(e);
      if (hay === undefined) {
        hay = JSON.stringify(e).toLowerCase();
        searchHay.set(e, hay);
      }
      return hay.includes(q);
    });
  }
  return list;
});

// 实际挂载到 DOM 的列表：只取前 renderCount 条，避免一次渲染上千节点
const visibleList = computed(() => filtered.value.slice(0, renderCount.value));

function onListScroll(e: Event) {
  const el = e.target as HTMLElement;
  if (el.scrollHeight - el.scrollTop - el.clientHeight < 200) {
    renderCount.value = Math.min(renderCount.value + RENDER_LIMIT, filtered.value.length);
  }
}

// 搜索/筛选条件变化时，重置可见数量，避免新筛选结果下仍挂载大量旧节点
watch([search, selectedTypes], () => {
  renderCount.value = RENDER_LIMIT;
});

function toggleType(t: string) {
  const s = new Set(selectedTypes.value);
  if (s.has(t)) s.delete(t);
  else s.add(t);
  selectedTypes.value = s;
}

// 事件唯一键：优先用服务端 ts+event+coin 组合，保证 SSE 回放与历史接口去重
function evKey(ev: any): string {
  const ts = ev.ts || ev.timestamp || ev.ts_ms || ev.time || '';
  return `${ts}|${ev.event || ''}|${ev.coin || ev.symbol || ''}|${ev.msg || ev.message || ''}`;
}

// (supplemental audit 2026-09-02) 取事件的毫秒时间戳；服务端 ts 为 epoch 毫秒，
// 兼容秒级(ts<1e12)*1000、ISO 字符串与无 ts（回退当前时间）。
function evMs(ev: any): number {
  const raw = ev.ts || ev.timestamp || ev.ts_ms || ev.time;
  if (raw === undefined || raw === null || raw === '') return Date.now();
  if (typeof raw === 'number') return raw < 1e12 ? raw * 1000 : raw;
  const t = new Date(raw).getTime();
  return isNaN(t) ? Date.now() : t;
}

// (supplemental audit 2026-09-02) 合并事件。
// live=true（SSE 实时通道）：新到事件按时间戳前插到顶部，并推进实时水位；
//   但早于水位的（连接回放/重连补播的旧事件）转入回填，避免几分钟前的旧
//   事件跳到列表顶部伪装成"刚滚动进来"。
// live=false（history/localStorage 初始化回填）：按时间戳降序归位，绝不前插。
function mergeEvents(incoming: any[], live = false) {
  if (!incoming.length) return;
  const existing = new Set(events.value.map(evKey));
  const liveAdds: any[] = [];
  const backfill: any[] = [];
  for (const e of incoming) {
    if (!e.ts && !e.timestamp) e.ts = Date.now();
    const k = evKey(e);
    if (existing.has(k)) continue;
    existing.add(k);
    if (live) {
      const ms = evMs(e);
      // 已建立实时水位后，早于水位的到达事件是回放/补播，按回填归位
      if (liveSeenMaxTs > 0 && ms < liveSeenMaxTs) backfill.push(e);
      else {
        liveAdds.push(e);
        if (ms > liveSeenMaxTs) liveSeenMaxTs = ms;
      }
    } else {
      backfill.push(e);
    }
  }
  if (!liveAdds.length && !backfill.length) return;
  let list = events.value;
  if (liveAdds.length) list = [...liveAdds, ...list];
  if (backfill.length) {
    // 回填按时间戳降序（newest-first）合并进列表，保持列表整体按时间倒序
    const bf = backfill.sort((a, b) => evMs(b) - evMs(a));
    list = [...list, ...bf].sort((a, b) => evMs(b) - evMs(a));
  }
  // 回填阶段（水位未建立）若回填事件比当前记录更新，顺带抬高水位基线，
  // 这样紧随其后的、早于该基线的回放事件不会被误判为实时
  if (liveSeenMaxTs === 0 && backfill.length) {
    liveSeenMaxTs = Math.max(...backfill.map(evMs));
  }
  events.value = list.slice(0, MAX_EVENTS);
  schedulePersist();
}

// SSE 单条事件高频到达时，先入队再微任务批量合并，避免每条都触发一次数组替换与渲染
function enqueueEvents(incoming: any[]) {
  for (const e of incoming) pendingBuffer.push(e);
  if (flushScheduled) return;
  flushScheduled = true;
  queueMicrotask(() => {
    flushScheduled = false;
    const batch = pendingBuffer;
    pendingBuffer = [];
    // (supplemental audit 2026-09-02) SSE 通道标记为 live；其中夹杂的
    // 连接回放/重连补播旧事件由 mergeEvents 内部按实时水位甄别为回填。
    if (batch.length) mergeEvents(batch, true);
  });
}

function schedulePersist() {
  if (persistTimer !== null) clearTimeout(persistTimer);
  persistTimer = window.setTimeout(() => {
    persistTimer = null;
    persistEvents();
  }, 3000);
}

function persistEvents() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(events.value.slice(0, STORAGE_MAX)));
  } catch {
    /* localStorage 满或禁用，忽略 */
  }
}

function flushPersistNow() {
  if (persistTimer !== null) {
    clearTimeout(persistTimer);
    persistTimer = null;
  }
  persistEvents();
}

function loadFromStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const arr = JSON.parse(raw);
      if (Array.isArray(arr) && arr.length) events.value = arr;
    }
  } catch {
    /* 数据损坏忽略 */
  }
}

// 从后端历史接口拉取完整近期记录，与本地/SSE 数据合并去重
async function loadHistory() {
  try {
    const { data } = await http.get('/api/portal/trader/api/feed/history', {
      params: { limit: MAX_EVENTS },
    });
    const list = data?.events || data || [];
    if (Array.isArray(list) && list.length) {
      // 历史接口返回 oldest-first，倒序为 newest-first 以匹配列表
      mergeEvents([...list].reverse());
    }
  } catch {
    /* 历史接口不可用，仅依赖 SSE */
  }
}

// (supplemental audit 2026-09-02) 事件用于显示的时间戳：scan 事件的 ts 是扫描
// 「完成落盘」时刻，冷扫描/限流退避时会比真正开始晚数十秒甚至数分钟。优先取
// start_ts_ms（扫描开始时刻）让时间列落在动作实际发生点；其余事件回退 ts。
function evDisplayTs(ev: any): number | string {
  if (ev?.event === 'scan' && ev.start_ts_ms) return ev.start_ts_ms;
  return ev.ts || ev.timestamp || ev.ts_ms || ev.time;
}

function evTime(ev: any): string {
  const ts = evDisplayTs(ev);
  if (!ts) return '';
  const d = new Date(typeof ts === 'number' ? (ts < 1e12 ? ts * 1000 : ts) : ts);
  return isNaN(d.getTime()) ? '' : d.toLocaleTimeString('zh-CN', { hour12: false });
}

function evDateTime(ev: any): string {
  const ts = evDisplayTs(ev);
  if (!ts) return '';
  const d = new Date(typeof ts === 'number' ? (ts < 1e12 ? ts * 1000 : ts) : ts);
  return isNaN(d.getTime()) ? '' : d.toLocaleString('zh-CN', { hour12: false });
}

function evSummary(ev: any): string {
  const parts: string[] = [];
  if (ev.coin) parts.push(String(ev.coin));
  if (ev.side) parts.push(SIDE_LABELS[ev.side] || String(ev.side));
  if (typeof ev.score === 'number') parts.push(`评分 ${ev.score.toFixed(2)}`);
  if (ev.verdict) parts.push(String(ev.verdict));
  if (ev.reason) parts.push(String(ev.reason));
  if (ev.msg) parts.push(String(ev.msg));
  if (ev.message) parts.push(String(ev.message));
  if (ev.detail) parts.push(String(ev.detail));
  if (ev.triggers !== undefined) parts.push(`${ev.triggers} 个触发`);
  if (ev.executed !== undefined) parts.push(ev.executed ? '已执行' : '未执行');
  return parts.length ? parts.join(' · ') : (ev.event || 'event');
}

function evDetailRows(ev: any): Array<[string, string]> {
  // (supplemental audit 2026-09-02) start_ts_ms 已用于时间列显示，不在详情里
  // 重复裸毫秒；scan_duration_ms 转成易读的秒/毫秒。
  const skip = new Set(['event', 'start_ts_ms']);
  const rows: Array<[string, string]> = [];
  for (const [k, v] of Object.entries(ev)) {
    if (skip.has(k)) continue;
    if (v === null || v === undefined || v === '') continue;
    let display: string;
    if (typeof v === 'object') {
      display = JSON.stringify(v);
    } else if (k === 'side' && SIDE_LABELS[String(v)]) {
      display = SIDE_LABELS[String(v)];
    } else if (k === 'scan_duration_ms' && typeof v === 'number') {
      display = v >= 1000 ? `${(v / 1000).toFixed(1)} 秒` : `${v} 毫秒`;
    } else {
      display = String(v);
    }
    rows.push([FIELD_LABELS[k] || k, display]);
  }
  return rows;
}

// ── 扫描模式 / 名单 ───────────────────────────────────────
async function loadScanConfig() {
  try {
    const { data } = await http.get('/api/portal/trader/api/dashboard/config');
    const cfg = data?.config || data || {};
    // 币种名单（开仓风控 gate 生效）
    const allow = cfg.coin_allowlist || [];
    const block = cfg.coin_blocklist || [];
    coinAllowlist.value = Array.isArray(allow) ? allow : [];
    coinBlocklist.value = Array.isArray(block) ? block : [];
    if (coinAllowlist.value.length > 0) scanMode.value = 'allowlist';
    else if (coinBlocklist.value.length > 0) scanMode.value = 'blocklist';
    else scanMode.value = 'all';
    // HIP-3 场馆名单（扫描聚合层生效，前置条件 enable_hip3 总开关）
    hip3Enabled.value = !!cfg.enable_hip3;
    const dexAllow = cfg.hip3_dex_allowlist || [];
    const dexBlock = cfg.hip3_dex_blocklist || [];
    dexAllowlist.value = Array.isArray(dexAllow) ? dexAllow : [];
    dexBlocklist.value = Array.isArray(dexBlock) ? dexBlock : [];
  } catch {
    /* 配置加载失败不阻塞页面 */
  }
}

async function setScanMode(mode: 'all' | 'allowlist' | 'blocklist') {
  scanModeSaving.value = true;
  const prevMode = scanMode.value;
  scanMode.value = mode;
  try {
    let updates: Record<string, string[]> = {};
    if (mode === 'all') {
      // 全局扫描：清空白名单和黑名单
      updates = { coin_allowlist: [], coin_blocklist: [] };
    } else if (mode === 'allowlist') {
      // 仅白名单：保留白名单（若为空则用当前黑名单的补集概念——需用户维护），清空黑名单
      updates = { coin_blocklist: [] };
      if (coinAllowlist.value.length === 0) {
        updates.coin_allowlist = [];
      }
    } else if (mode === 'blocklist') {
      // 除黑名单外全部：清空白名单，保留黑名单
      updates = { coin_allowlist: [] };
    }
    await http.post('/api/portal/trader/api/dashboard/config', { updates });
    await loadScanConfig();
    toast.ok(`扫描模式已切换为：${SCAN_MODE_LABELS[mode]}`);
  } catch {
    scanMode.value = prevMode;
    toast.err('扫描模式切换失败');
  } finally {
    scanModeSaving.value = false;
  }
}

function addCoin(list: 'allowlist' | 'blocklist') {
  const coin = coinInput.value.trim().toUpperCase();
  if (!coin) return;
  const arr = list === 'allowlist' ? coinAllowlist.value : coinBlocklist.value;
  if (!arr.includes(coin)) {
    arr.push(coin);
    saveCoinLists();
  }
  coinInput.value = '';
}

function removeCoin(list: 'allowlist' | 'blocklist', idx: number) {
  const arr = list === 'allowlist' ? coinAllowlist.value : coinBlocklist.value;
  arr.splice(idx, 1);
  saveCoinLists();
}

async function saveCoinLists() {
  scanModeSaving.value = true;
  try {
    await http.post('/api/portal/trader/api/dashboard/config', {
      updates: {
        coin_allowlist: coinAllowlist.value,
        coin_blocklist: coinBlocklist.value,
      },
    });
  } catch {
    toast.err('币种列表保存失败');
  } finally {
    scanModeSaving.value = false;
  }
}

// ── HIP-3 场馆（DEX）名单 ─────────────────────────────────
function addDex(list: 'allowlist' | 'blocklist') {
  const inputRef = list === 'allowlist' ? dexAllowInput : dexBlockInput;
  const dex = inputRef.value.trim().toLowerCase();
  if (!dex) return;
  const arr = list === 'allowlist' ? dexAllowlist.value : dexBlocklist.value;
  if (!arr.includes(dex)) {
    arr.push(dex);
    saveDexLists();
  }
  inputRef.value = '';
}

function removeDex(list: 'allowlist' | 'blocklist', idx: number) {
  const arr = list === 'allowlist' ? dexAllowlist.value : dexBlocklist.value;
  arr.splice(idx, 1);
  saveDexLists();
}

async function saveDexLists() {
  scanModeSaving.value = true;
  try {
    await http.post('/api/portal/trader/api/dashboard/config', {
      updates: {
        hip3_dex_allowlist: dexAllowlist.value,
        hip3_dex_blocklist: dexBlocklist.value,
      },
    });
    toast.ok('HIP-3 场馆名单已保存（扫描层热生效）');
  } catch {
    toast.err('HIP-3 场馆名单保存失败');
  } finally {
    scanModeSaving.value = false;
  }
}

function clearAll() {
  events.value = [];
  pendingBuffer = [];
  renderCount.value = RENDER_LIMIT;
  selectedEvent.value = null;
  try { localStorage.removeItem(STORAGE_KEY); } catch {}
}

let unsubSse: (() => void) | null = null;

onMounted(() => {
  // 先从本地存储恢复，再从后端拉取完整近期历史（SSE 连接建立后还会实时追加）
  loadFromStorage();
  loadHistory();
  loadScanConfig();
  // 订阅全局 SSE 事件流（连接由 AppShell 启动，全程单例）。
  // paused 仅控制本页列表追加，不影响全局弹窗/语音。
  unsubSse = sseFeed.onEvent((data) => {
    if (!paused.value) enqueueEvents([data]);
  });
  // 页面隐藏/卸载前把缓冲落盘，避免丢事件
  window.addEventListener('pagehide', flushPersistNow);
});

onUnmounted(() => {
  unsubSse?.();
  unsubSse = null;
  flushPersistNow();
  window.removeEventListener('pagehide', flushPersistNow);
});
</script>

<template>
  <div class="space-y-4">
    <header class="flex items-center justify-between flex-wrap gap-3">
      <div>
        <h2 class="text-xl font-semibold">渠道消息</h2>
        <p class="text-sm text-[var(--text-muted)] mt-1">智能体实时事件流 · SSE 推送</p>
      </div>
      <div class="flex items-center gap-3 text-sm">
        <span class="flex items-center gap-1.5">
          <span class="w-2 h-2 rounded-full" :class="connected ? 'bg-emerald-500 animate-pulse' : 'bg-rose-500'"></span>
          <span class="text-[var(--text-muted)]">{{ connected ? '已连接' : (reconnecting ? '重连中…' : '未连接') }}</span>
        </span>
        <span class="text-[var(--text-muted)]">{{ filtered.length }} / {{ events.length }} 条</span>
        <button class="btn text-xs" @click="sseFeed.manualReconnect()" :disabled="reconnecting">🔄 重连</button>
        <button class="btn text-xs" @click="paused = !paused">{{ paused ? '▶️ 继续' : '⏸️ 暂停' }}</button>
        <button class="btn text-xs" @click="clearAll">清空</button>
      </div>
    </header>

    <!-- 扫描过滤（紧凑卡）：币种名单=开仓风控层；HIP-3 场馆名单=扫描聚合层 -->
    <div class="card !py-2 space-y-1.5" title="币种名单：下单前风控 gate 生效（黑名单必拦、白名单非空则只放行名单内），不缩小扫描范围；HIP-3 场馆名单：被排除场馆在选币前即剔除，不扫描、不产生事件，主 DEX 加密市场不受影响。均热生效、与系统配置同源。">
      <!-- 币种过滤（开仓风控层） -->
      <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span class="text-[11px] text-[var(--text-muted)] font-medium whitespace-nowrap">币种</span>
        <div class="flex rounded-md border border-[var(--border)] overflow-hidden">
          <button
            v-for="m in SCAN_MODES"
            :key="m.key"
            class="px-2 py-0.5 text-[11px] transition-all"
            :class="scanMode === m.key
              ? 'bg-cyan-500/20 text-cyan-300 font-medium'
              : 'text-[var(--text-muted)] hover:bg-white/5'"
            :disabled="scanModeSaving"
            @click="setScanMode(m.key)"
          >
            {{ m.label }}
          </button>
        </div>
        <span
          v-for="(c, i) in coinAllowlist"
          :key="'a'+c"
          class="inline-flex items-center px-1.5 py-0 rounded text-[11px] bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 cursor-pointer"
          :class="scanMode === 'allowlist' ? '' : 'opacity-40 pointer-events-none'"
          title="白名单 · 点击移除"
          @click="removeCoin('allowlist', i)"
        >{{ c }} ×</span>
        <span
          v-for="(c, i) in coinBlocklist"
          :key="'b'+c"
          class="inline-flex items-center px-1.5 py-0 rounded text-[11px] bg-rose-500/15 text-rose-300 border border-rose-500/30 cursor-pointer"
          :class="scanMode === 'blocklist' ? '' : 'opacity-40 pointer-events-none'"
          title="黑名单 · 点击移除"
          @click="removeCoin('blocklist', i)"
        >{{ c }} ×</span>
        <input
          v-model="coinInput"
          :placeholder="scanMode === 'blocklist' ? '拉黑币种…' : '加白币种…'"
          class="input !py-0.5 !px-1.5 text-[11px] w-20"
          :disabled="scanMode === 'all'"
          @keydown.enter="addCoin(scanMode === 'blocklist' ? 'blocklist' : 'allowlist')"
        />
        <span v-if="scanModeSaving" class="text-[10px] text-[var(--text-muted)] animate-pulse">保存中…</span>
        <span v-else class="text-[10px] text-[var(--text-muted)] opacity-50 ml-auto" title="黑名单币种仍会被扫描并产生事件，仅在下单前拦截">开仓时拦截 · 不缩小扫描</span>
      </div>

      <!-- HIP-3 场馆过滤（扫描聚合层；enable_hip3=false 时整层不生效） -->
      <div
        class="flex flex-wrap items-center gap-x-2 gap-y-1"
        :class="hip3Enabled ? '' : 'opacity-50'"
      >
        <span class="text-[11px] text-[var(--text-muted)] font-medium whitespace-nowrap">HIP-3 场馆</span>
        <span
          v-if="!hip3Enabled"
          class="text-[10px] text-amber-300/90 bg-amber-500/10 border border-amber-500/30 rounded px-1.5 py-0"
          title="系统配置 enable_hip3=false 时，扫描链路不加载任何 HIP-3 场馆市场，此名单配了也不生效。去 /portal/config 开启 enable_hip3。"
        >⚠ enable_hip3 未开启 · 名单暂不生效</span>
        <span
          v-for="(d, i) in dexAllowlist"
          :key="'da'+d"
          class="inline-flex items-center px-1.5 py-0 rounded text-[11px] bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 cursor-pointer"
          title="只扫这些场馆 · 点击移除"
          @click="removeDex('allowlist', i)"
        >{{ d }} ×</span>
        <span
          v-for="(d, i) in dexBlocklist"
          :key="'db'+d"
          class="inline-flex items-center px-1.5 py-0 rounded text-[11px] bg-rose-500/15 text-rose-300 border border-rose-500/30 cursor-pointer"
          title="排除这些场馆 · 点击移除"
          @click="removeDex('blocklist', i)"
        >{{ d }} ×</span>
        <input
          v-model="dexAllowInput"
          placeholder="只扫场馆，如 xyz…"
          class="input !py-0.5 !px-1.5 text-[11px] w-28"
          @keydown.enter="addDex('allowlist')"
        />
        <input
          v-model="dexBlockInput"
          placeholder="排除场馆，如 km…"
          class="input !py-0.5 !px-1.5 text-[11px] w-28"
          @keydown.enter="addDex('blocklist')"
        />
        <span class="text-[10px] text-[var(--text-muted)] opacity-50 ml-auto" title="仅影响 HIP-3 子场馆（代币化股票/商品，xyz/km/vntl）；被排除场馆不扫描、不产生事件">扫描层剔除 · 主 DEX 不受影响</span>
      </div>
    </div>

    <!-- 过滤条 -->
    <div class="card !py-2 flex flex-wrap items-center gap-1.5">
      <button
        v-for="t in eventTypes"
        :key="t.key"
        :title="t.key === 'near_miss' ? 'near_miss：评分达到开仓门槛 70% 但未触发开仓（crypto 与 HIP-3 市场均计入）' : `只看「${t.label}」事件（再点取消）`"
        class="group px-2.5 py-1 text-xs rounded-full border transition-all duration-150 flex items-center gap-1.5 active:scale-95"
        :class="selectedTypes.has(t.key)
          ? [EVENT_COLORS[t.key], 'ring-1 ring-white/25 shadow-sm font-medium']
          : 'text-[var(--text-muted)] border-[var(--border)] hover:bg-white/5 hover:text-[var(--text)]'"
        @click="toggleType(t.key)"
      >
        {{ t.label }}
        <span
          v-if="counts.get(t.key)"
          class="tabular-nums text-[10px] leading-none px-1.5 py-0.5 rounded-full transition-colors"
          :class="selectedTypes.has(t.key)
            ? 'bg-white/20 font-semibold'
            : 'bg-white/5 text-[var(--text-muted)] group-hover:bg-white/10'"
        >{{ counts.get(t.key) }}</span>
      </button>
      <input
        v-model="search"
        placeholder="搜索 coin / reason / msg ..."
        class="input ml-auto w-40 sm:w-56 lg:w-60"
      />
    </div>

    <!-- 事件流 + 详情 -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <!-- 事件列表 -->
      <div class="card lg:col-span-2 p-0 overflow-hidden">
        <div class="bg-black/40 h-[50vh] lg:h-[calc(100vh-340px)] min-h-[280px] lg:min-h-[500px] overflow-y-auto" @scroll="onListScroll">
          <div v-if="filtered.length === 0" class="text-center text-[var(--text-muted)] py-20 text-sm">
            <div class="animate-pulse">等待事件...</div>
            <div class="text-xs mt-2 opacity-60">SSE 连接中，智能体事件将实时推送</div>
          </div>
          <div
            v-for="ev in visibleList"
            :key="evKey(ev)"
            class="flex items-start gap-2 px-3 py-2 border-b border-[var(--border)] cursor-pointer hover:bg-white/5 transition-colors"
            :class="selectedEvent === ev ? 'bg-white/10' : ''"
            @click="selectedEvent = ev"
          >
            <span class="text-[10px] text-[var(--text-muted)] font-mono mt-0.5 whitespace-nowrap">{{ evTime(ev) }}</span>
            <span
              class="text-[10px] px-1.5 py-0.5 rounded font-medium whitespace-nowrap border"
              :class="EVENT_COLORS[ev.event] || 'text-slate-300 bg-slate-500/10 border-slate-500/30'"
            >
              {{ EVENT_TYPE_LABELS[ev.event] || ev.event || '事件' }}
            </span>
            <span class="text-xs flex-1 break-words text-slate-200 leading-relaxed">{{ evSummary(ev) }}</span>
          </div>
          <div v-if="visibleList.length < filtered.length" class="text-center text-[10px] text-[var(--text-muted)] py-2 opacity-60">
            向下滚动加载更多 · 已显示 {{ visibleList.length }}/{{ filtered.length }}
          </div>
        </div>
      </div>

      <!-- 详情面板 -->
      <div class="card p-0 overflow-hidden">
        <div class="bg-black/40 h-[50vh] lg:h-[calc(100vh-340px)] min-h-[280px] lg:min-h-[500px] overflow-y-auto p-4">
          <div v-if="!selectedEvent" class="text-center text-[var(--text-muted)] py-20 text-sm">
            点击左侧事件查看详情
          </div>
          <div v-else>
            <div class="flex items-center gap-2 mb-4 pb-3 border-b border-[var(--border)]">
              <span
                class="text-xs px-2 py-0.5 rounded font-medium border"
                :class="EVENT_COLORS[selectedEvent.event] || 'text-slate-300 bg-slate-500/10 border-slate-500/30'"
              >
                {{ EVENT_TYPE_LABELS[selectedEvent.event] || selectedEvent.event || '事件' }}
              </span>
              <span class="text-xs text-[var(--text-muted)] ml-auto">{{ evDateTime(selectedEvent) }}</span>
            </div>
            <table class="w-full text-xs">
              <tbody>
                <tr
                  v-for="[k, v] in evDetailRows(selectedEvent)"
                  :key="k"
                  class="border-b border-[var(--border)]/50 align-top"
                >
                  <td class="py-2 pr-3 text-[var(--text-muted)] whitespace-nowrap font-mono w-24">{{ k }}</td>
                  <td class="py-2 text-slate-200 break-all font-mono leading-relaxed">{{ v }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
