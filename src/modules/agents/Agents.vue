<script setup lang="ts">
/**
 * 智能体编排控制台
 * - 状态数据：hermes-trader /api/agent/state（权益、胜率、最近感知/研判/成交）
 * - 运行模式：/api/dashboard/summary（status 字段反映扫描状态）
 * - 实时事件：sseFeed 单例（AppShell 登录后统一建连，本页 onEvent 订阅）
 * - 写操作（启停/扫描）需要 agent:control 权限
 */
import { computed, onMounted, onUnmounted, ref } from 'vue';
import http from '@/shared/api/client';
import { useAuthStore } from '@/stores/auth';
import { useToast } from '@/stores/toast';
import { useSseFeedStore } from '@/stores/sseFeed';

const auth = useAuthStore();
const toast = useToast();

const state = ref<any>(null);
const summary = ref<any>(null);
const config = ref<any>(null);
const events = ref<any[]>([]);
const scanResults = ref<any[]>([]);
const scanCount = ref(0);
const scanning = ref(false);
const scanDone = ref(false);
const loading = ref(true);
const busy = ref(false);
const scanCoin = ref('BTC');
const paused = ref(false);
let timer: number | null = null;
let sseUnsub: (() => void) | null = null;
const MAX_EVENTS = 200;

const canControl = computed(() => auth.hasPermission('agent:control'));

// 运行状态从 summary.status 推导
const running = computed(() => {
  const s = summary.value?.status;
  return s === 'scanning' || s === 'running';
});
const modeLabel = computed(() => {
  const m = state.value?.config?.mode || summary.value?.mode;
  if (!m) return '—';
  const map: Record<string, string> = { live: '实盘', paper: '模拟', dry: '空跑', shadow: '影子' };
  return map[m] || m;
});
const winRate = computed(() => {
  const wr = state.value?.win_rate;
  if (!wr) return null;
  const total = (wr.wins || 0) + (wr.losses || 0);
  return total ? { ...wr, total, pct: (wr.wins / total) * 100 } : null;
});

async function load() {
  try {
    const [s, c, sum] = await Promise.all([
      http.get('/api/portal/trader/api/agent/state'),
      http.get('/api/portal/trader/api/agent/config'),
      http.get('/api/portal/trader/api/dashboard/summary'),
    ]);
    state.value = s.data;
    config.value = c.data;
    summary.value = sum.data;
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '加载智能体状态失败');
  } finally {
    loading.value = false;
  }
}

async function control(action: 'start' | 'stop') {
  busy.value = true;
  try {
    await http.post(`/api/portal/trader/api/agent/${action}`);
    toast.ok(`智能体${action === 'start' ? '已启动' : '已停止'}`);
    await load();
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '操作失败');
  } finally {
    busy.value = false;
  }
}

async function scan() {
  scanning.value = true;
  busy.value = true;
  const coin = scanCoin.value.trim().toUpperCase();
  try {
    const payload: Record<string, any> = { minScore: 20 };
    if (coin) payload.coin = coin;
    const { data } = await http.post(
      '/api/portal/trader/api/agent/scan',
      payload,
      { timeout: 180000 },
    );
    const perceptions: any[] = data?.perceptions || [];
    scanResults.value = perceptions;
    scanCount.value = data?.count ?? perceptions.length;
    scanDone.value = true;
    if (perceptions.length) {
      const coins = perceptions.map((p) => p.coin || p.symbol || p.ticker || '?').join(', ');
      toast.ok(coin
        ? `扫描完成：${coin} 综合评分 ${perceptions[0]?.composite_score ?? '—'}`
        : `扫描完成：发现 ${perceptions.length} 个机会（${coins}）`);
    } else {
      toast.ok(coin
        ? `扫描完成：${coin} 未达到阈值（minScore 20）`
        : '扫描完成：本次未发现符合阈值的机会');
    }
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '扫描触发失败（全市场扫描约需 60-120 秒，请耐心等待）');
  } finally {
    scanning.value = false;
    busy.value = false;
  }
}

// ---- SSE 事件解析 ----
const EVENT_LABELS: Record<string, string> = {
  scan: '扫描', research: '研判', execute: '执行', 'execute-fail': '执行失败',
  ta_skip: '跳过', dsl_exit: '退出', loop_heartbeat: '心跳', loop_start: '循环', error: '错误',
};
const EVENT_COLORS: Record<string, string> = {
  scan: 'text-cyan-300 bg-cyan-500/10', research: 'text-violet-300 bg-violet-500/10',
  execute: 'text-emerald-300 bg-emerald-500/10', 'execute-fail': 'text-rose-300 bg-rose-500/10',
  ta_skip: 'text-slate-400 bg-slate-500/10', dsl_exit: 'text-amber-300 bg-amber-500/10',
  loop_heartbeat: 'text-slate-500 bg-slate-500/5', loop_start: 'text-sky-300 bg-sky-500/10',
  error: 'text-rose-400 bg-rose-500/15',
};

// 扫描综合评分三态：真实数值（含 0 分）返回 number；上游缺失（null/undefined/
// 非数字）返回 null，由模板渲染为 '—'，避免把"无数据"伪装成 0.00 误导研判。
// (supplemental audit 2026-08-31 F6)
function scoreOf(p: any): number | null {
  const v = p?.composite_score ?? p?.score;
  return typeof v === 'number' && !Number.isNaN(v) ? v : null;
}

function evSummary(ev: any): string {
  const parts: string[] = [];
  if (ev.coin) parts.push(ev.coin);
  if (ev.side) parts.push(ev.side === 'long' ? '做多' : '做空');
  if (typeof ev.score === 'number') parts.push(`评分 ${ev.score.toFixed(2)}`);
  if (ev.verdict) parts.push(ev.verdict);
  if (ev.reason) parts.push(ev.reason);
  if (ev.msg) parts.push(ev.msg);
  if (ev.message) parts.push(ev.message);
  return parts.length ? parts.join(' · ') : (ev.event || 'event');
}

function evTime(ev: any): string {
  const ts = ev.ts || ev.timestamp || ev.time;
  if (!ts) return '';
  const d = new Date(typeof ts === 'number' ? ts : ts);
  return isNaN(d.getTime()) ? '' : d.toLocaleTimeString('zh-CN', { hour12: false });
}

function evDetail(ev: any): Array<[string, string]> {
  const skip = new Set(['event', 'ts', 'timestamp', 'time']);
  const rows: Array<[string, string]> = [];
  for (const [k, v] of Object.entries(ev)) {
    if (skip.has(k)) continue;
    if (v === null || v === undefined || v === '') continue;
    if (typeof v === 'object') {
      rows.push([k, JSON.stringify(v)]);
    } else {
      rows.push([k, String(v)]);
    }
  }
  return rows;
}

const selectedIdx = ref<number | null>(null);
const selectedEvent = computed(() => selectedIdx.value !== null ? events.value[selectedIdx.value] : null);

// 实时事件统一走 sseFeed 单例（AppShell 登录后启动，负责取票/指数退避重连/45s 心跳看门狗），
// 本页仅订阅，不再自建第二条 EventSource。
onMounted(() => {
  load();
  const sseFeed = useSseFeedStore();
  sseUnsub = sseFeed.onEvent((data) => {
    if (paused.value) return;
    events.value.unshift(data);
    if (events.value.length > MAX_EVENTS) events.value = events.value.slice(0, MAX_EVENTS);
  });
  timer = window.setInterval(load, 15000);
});
onUnmounted(() => {
  if (sseUnsub) { sseUnsub(); sseUnsub = null; }
  if (timer) window.clearInterval(timer);
});
</script>

<template>
  <div class="space-y-5">
    <header>
      <h2 class="text-xl font-semibold">智能体编排控制台</h2>
      <p class="text-sm text-[var(--text-muted)] mt-1">多智能体运行状态、启停控制与实时事件流</p>
    </header>

    <div v-if="loading" class="card text-center py-10 text-[var(--text-muted)]">加载中...</div>

    <template v-else>
      <!-- 状态卡片 -->
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">运行状态</div>
          <div class="mt-2 flex items-center gap-2">
            <span class="w-3 h-3 rounded-full" :class="running ? 'bg-emerald-500 animate-pulse' : 'bg-rose-500'"></span>
            <span class="text-lg font-semibold">{{ running ? '运行中' : '已停止' }}</span>
          </div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">当前模式</div>
          <div class="text-lg font-semibold mt-2">{{ modeLabel }}</div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">账户权益</div>
          <div class="text-lg font-semibold mt-2">${{ Number(state?.equity ?? 0).toFixed(2) }}</div>
          <div class="text-xs mt-1" :class="(state?.daily_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'">
            今日 {{ (state?.daily_pnl ?? 0) >= 0 ? '+' : '' }}{{ Number(state?.daily_pnl ?? 0).toFixed(2) }}
          </div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">胜率</div>
          <div class="text-lg font-semibold mt-2">{{ winRate ? winRate.pct.toFixed(1) + '%' : '—' }}</div>
          <div v-if="winRate" class="text-xs text-[var(--text-muted)] mt-1">{{ winRate.wins }}胜 / {{ winRate.losses }}负 ({{ winRate.total }})</div>
        </div>
      </div>

      <!-- 控制条 -->
      <div class="card flex flex-wrap items-center gap-3">
        <button class="btn btn-primary" :disabled="!canControl || busy || running" @click="control('start')">▶️ 启动</button>
        <button class="btn btn-danger" :disabled="!canControl || busy || !running" @click="control('stop')">⏹️ 停止</button>
        <div class="flex items-center gap-2 ml-auto">
          <input class="input" v-model="scanCoin" placeholder="币种" style="width:100px" />
          <button class="btn" :disabled="!canControl || busy" @click="scan">
            {{ scanning ? '⏳ 扫描中（约60-120秒）...' : '🔍 触发扫描' }}
          </button>
        </div>
      </div>

      <!-- 扫描结果 -->
      <div v-if="scanResults.length" class="card">
        <div class="flex items-center justify-between mb-3">
          <h3 class="font-semibold">最近扫描结果</h3>
          <span class="text-xs text-[var(--text-muted)]">发现 {{ scanCount }} 个机会</span>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-xs">
            <thead>
              <tr class="text-[var(--text-muted)] border-b border-[var(--border)]">
                <th class="text-left py-2 px-2">币种</th>
                <th class="text-left py-2 px-2">方向</th>
                <th class="text-right py-2 px-2">综合评分</th>
                <th class="text-left py-2 px-2">信号</th>
                <th class="text-left py-2 px-2">摘要</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(p, i) in scanResults" :key="i" class="border-b border-[var(--border)]/50 hover:bg-white/5">
                <td class="py-2 px-2 font-semibold">{{ p.coin || p.symbol || p.ticker || '—' }}</td>
                <td class="py-2 px-2">
                  <span class="px-1.5 py-0.5 rounded text-[10px]"
                    :class="(p.side || p.direction || '').toLowerCase().includes('short') ? 'bg-rose-500/15 text-rose-300' : 'bg-emerald-500/15 text-emerald-300'">
                    {{ p.side || p.direction || '—' }}
                  </span>
                </td>
                <td class="py-2 px-2 text-right font-mono">
                  <span v-if="scoreOf(p) !== null" :class="(scoreOf(p) as number) >= 30 ? 'text-amber-300 font-bold' : ''">
                    {{ (scoreOf(p) as number).toFixed(2) }}
                  </span>
                  <span v-else class="text-[var(--text-muted)]">—</span>
                </td>
                <td class="py-2 px-2 text-[var(--text-muted)]">{{ p.verdict || p.signal || '—' }}</td>
                <td class="py-2 px-2 text-slate-300 max-w-md truncate">{{ p.reason || p.summary || p.note || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <div v-else-if="scanDone && !scanning" class="card text-center py-6 text-sm text-[var(--text-muted)]">
        本次扫描未发现符合阈值的机会
      </div>

      <!-- 实时事件流 -->
      <div class="card">
        <div class="flex items-center justify-between mb-3">
          <h3 class="font-semibold">实时事件流</h3>
          <div class="flex items-center gap-2 text-sm">
            <span class="text-xs text-[var(--text-muted)]">{{ events.length }} 条</span>
            <button class="btn text-xs" @click="paused = !paused">{{ paused ? '▶️ 继续' : '⏸️ 暂停' }}</button>
            <button class="btn text-xs" @click="events = []; selectedIdx = null">清空</button>
          </div>
        </div>
        <div class="grid md:grid-cols-2 gap-4">
          <!-- 事件列表 -->
          <div class="bg-black/40 rounded-lg h-96 overflow-y-auto">
            <div v-if="events.length === 0" class="text-center text-[var(--text-muted)] py-16 text-sm">
              <div class="animate-pulse">等待事件...</div>
              <div class="text-xs mt-2 opacity-60">SSE 连接中，智能体事件将实时推送</div>
            </div>
            <div v-else>
              <div v-for="(ev, i) in events" :key="i"
                class="flex items-start gap-2 px-3 py-2 border-b border-[var(--border)] cursor-pointer hover:bg-white/5 transition-colors"
                :class="selectedIdx === i ? 'bg-white/10' : ''"
                @click="selectedIdx = i">
                <span class="text-[10px] text-[var(--text-muted)] font-mono mt-0.5 whitespace-nowrap">{{ evTime(ev) }}</span>
                <span class="text-[10px] px-1.5 py-0.5 rounded font-medium whitespace-nowrap" :class="EVENT_COLORS[ev.event] || 'text-slate-300 bg-slate-500/10'">
                  {{ EVENT_LABELS[ev.event] || ev.event || '事件' }}
                </span>
                <span class="text-xs flex-1 truncate text-slate-200">{{ evSummary(ev) }}</span>
              </div>
            </div>
          </div>
          <!-- 事件详情 -->
          <div class="bg-black/40 rounded-lg h-96 overflow-y-auto p-3">
            <div v-if="!selectedEvent" class="text-center text-[var(--text-muted)] py-16 text-sm">点击左侧事件查看详情</div>
            <div v-else>
              <div class="flex items-center gap-2 mb-3 pb-2 border-b border-[var(--border)]">
                <span class="text-sm px-2 py-0.5 rounded font-medium" :class="EVENT_COLORS[selectedEvent.event] || 'text-slate-300 bg-slate-500/10'">
                  {{ EVENT_LABELS[selectedEvent.event] || selectedEvent.event || '事件' }}
                </span>
                <span class="text-xs text-[var(--text-muted)]">{{ evTime(selectedEvent) }}</span>
              </div>
              <table class="w-full text-xs">
                <tbody>
                  <tr v-for="[k, v] in evDetail(selectedEvent)" :key="k" class="border-b border-[var(--border)]/50 align-top">
                    <td class="py-1.5 pr-3 text-[var(--text-muted)] whitespace-nowrap font-mono">{{ k }}</td>
                    <td class="py-1.5 text-slate-200 break-all font-mono">{{ v }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
