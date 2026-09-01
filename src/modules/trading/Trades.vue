<script setup lang="ts">
/**
 * 交易历史
 * - 数据来源：hermes-trader /api/dashboard/closed-trades（经 BFF 代理，含完整 PnL/杠杆/手续费）
 * - 统计：交易笔数、胜率、平均盈亏、净盈亏
 * - 过滤：来源(dsl/manual/external/reconcile/ai)、多空(long/short)、盈亏(win/loss)、币种搜索
 */
import { computed, onMounted, onUnmounted, ref } from 'vue';
import http from '@/shared/api/client';
import { useToast } from '@/stores/toast';
import { useSseFeedStore } from '@/stores/sseFeed';

const toast = useToast();
const loading = ref(true);
const error = ref('');
const trades = ref<any[]>([]);
const limit = ref(100);
const filterCoin = ref('');
const sourceFilter = ref<'all' | 'dsl' | 'manual' | 'external' | 'reconcile' | 'ai'>('all');
const sideFilter = ref<'all' | 'long' | 'short'>('all');
const outcomeFilter = ref<'all' | 'win' | 'loss'>('all');
const page = ref(1);
const pageSize = 20;
let timer: number | null = null;
let sseUnsub: (() => void) | null = null;
let pendingRefresh: number | null = null;

// SSE 事件驱动刷新：平仓相关事件（dsl_exit/ai_close/close_position/hard_killswitch）
// 或 position_update 到达时触发防抖拉取，将 trades 表刷新从 15s 轮询降到 SSE transit + 500ms。
// 'ws_user_fill' (Phase 2): exchange-fill WS 事件，比下一轮 scan 的 dsl_exit 提前到达。
const REFRESH_EVENTS = new Set(['dsl_exit', 'ai_close', 'close_position', 'hard_killswitch', 'position_update', 'ws_user_fill']);
function scheduleRefresh() {
  if (pendingRefresh !== null) return;
  pendingRefresh = window.setTimeout(() => {
    pendingRefresh = null;
    load();
  }, 500);
}

const filtered = computed(() => {
  let list = trades.value;
  if (filterCoin.value) {
    const q = filterCoin.value.toUpperCase();
    list = list.filter((t) => (t.coin || '').toUpperCase().includes(q));
  }
  if (sourceFilter.value !== 'all') list = list.filter((t) => t.source === sourceFilter.value);
  if (sideFilter.value !== 'all') list = list.filter((t) => t.side === sideFilter.value);
  if (outcomeFilter.value !== 'all') {
    list = list.filter((t) => (t.pnl_pct >= 0) === (outcomeFilter.value === 'win'));
  }
  return list;
});

const stats = computed(() => {
  const list = filtered.value;
  const wins = list.filter((t) => t.pnl_pct > 0);
  const losses = list.filter((t) => t.pnl_pct < 0);
  const net = list.reduce((s, t) => s + (t.pnl_pct || 0), 0);
  const avgWin = wins.length ? wins.reduce((s, t) => s + t.pnl_pct, 0) / wins.length : 0;
  const avgLoss = losses.length ? losses.reduce((s, t) => s + t.pnl_pct, 0) / losses.length : 0;
  const fees = list.reduce((s, t) => s + (t.fees_pct || 0), 0);
  return {
    count: list.length,
    winRate: list.length ? (wins.length / list.length) * 100 : 0,
    net,
    avgWin,
    avgLoss,
    fees,
    wins: wins.length,
    losses: losses.length,
  };
});

const paged = computed(() => filtered.value.slice((page.value - 1) * pageSize, page.value * pageSize));

async function load() {
  // 仅首次加载显示遮罩；后续 60s 轮询静默更新，避免整表闪烁
  if (trades.value.length === 0) loading.value = true;
  try {
    const { data } = await http.get('/api/portal/trader/api/dashboard/closed-trades', {
      params: { limit: limit.value },
    });
    const incoming: any[] = Array.isArray(data) ? data : (data?.trades || []);
    // 以 ts+coin 作为稳定身份做增量合并，保留未变化行的对象引用以减少 Vue patch
    const keyOf = (t: any) => `${t.ts}-${t.coin}`;
    const prev = new Map(trades.value.map((t) => [keyOf(t), t]));
    trades.value = incoming.map((nt) => {
      const old = prev.get(keyOf(nt));
      if (!old) return nt;
      const keys = Object.keys(nt);
      let changed = keys.length !== Object.keys(old).length;
      if (!changed) {
        for (const k of keys) {
          if (old[k] !== nt[k]) { changed = true; break; }
        }
      }
      return changed ? { ...old, ...nt } : old;
    });
    error.value = '';
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '加载交易历史失败';
  } finally {
    loading.value = false;
  }
}

function fmtTime(t: any) {
  if (!t) return '—';
  const d = new Date(typeof t === 'number' ? t : t);
  return isNaN(d.getTime()) ? String(t) : d.toLocaleString('zh-CN', { hour12: false });
}
function fmtNum(v: any, d = 2) {
  if (v === null || v === undefined || isNaN(Number(v))) return '—';
  return Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}
function pctClass(v: any) {
  if (v === null || v === undefined || isNaN(Number(v))) return '';
  return Number(v) > 0 ? 'text-emerald-400' : Number(v) < 0 ? 'text-rose-400' : '';
}
function reasonText(r: string) {
  if (!r) return '—';
  const map: Record<string, string> = {
    max_loss: '最大亏损止损',
    take_profit: '止盈',
    external_close_backfill: '外部平仓回填',
    external_close_recorded: '外部平仓记录',
    hard_timeout: '超时强平',
    stale_flat: '盘口静止平仓',
    signal_reverse: '信号反转',
    manual: '手动平仓',
    exchange_trigger: '交易所触发',
    exchange_trigger_manual_backfill: '交易所触发(回填)',
    reconcile_backfill: '对账回填',
    ai_close: 'AI平仓',
  };
  for (const [k, v] of Object.entries(map)) {
    if (r.startsWith(k)) return v;
  }
  return r;
}

const SOURCE_LABELS: Record<string, string> = {
  all: '全部',
  dsl: 'DSL',
  manual: '手动',
  external: '外部',
  reconcile: '对账',
  ai: 'AI',
};
function sourceLabel(s: string) {
  return SOURCE_LABELS[s] || s;
}
function sourceBadgeClass(s: string) {
  if (s === 'manual') return 'badge-warn';
  if (s === 'external') return 'badge-info';
  if (s === 'reconcile') return 'badge-ok';
  if (s === 'ai') return 'badge-muted';
  return 'badge-muted';
}

onMounted(() => {
  load();
  // SSE 事件驱动：平仓事件到达时立即刷新 trades 表（防抖 500ms）
  const sse = useSseFeedStore();
  sseUnsub = sse.onEvent((data) => {
    if (data?.event && REFRESH_EVENTS.has(data.event)) scheduleRefresh();
  });
  // 60s 兜底轮询：SSE 断连或事件丢失时仍能保持数据新鲜
  timer = window.setInterval(load, 60000);
});
onUnmounted(() => {
  if (timer) window.clearInterval(timer);
  if (pendingRefresh !== null) window.clearTimeout(pendingRefresh);
  sseUnsub?.();
  sseUnsub = null;
});
</script>

<template>
  <div class="space-y-5">
    <header class="flex items-center justify-between flex-wrap gap-3">
      <div>
        <h2 class="text-xl font-semibold">交易历史</h2>
        <p class="text-sm text-[var(--text-muted)] mt-1">已平仓交易记录（含 PnL、杠杆、手续费）</p>
      </div>
      <div class="flex gap-2 items-center flex-wrap">
        <input class="input" v-model="filterCoin" placeholder="筛选币种..." style="width:120px" />
        <select class="input" v-model.number="limit" @change="load">
          <option :value="50">50 条</option>
          <option :value="100">100 条</option>
          <option :value="200">200 条</option>
        </select>
        <button class="btn" @click="load">🔄</button>
      </div>
    </header>

    <!-- 统计卡片 -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div class="card">
        <div class="text-xs text-[var(--text-muted)]">交易笔数</div>
        <div class="text-2xl font-semibold mt-1">{{ stats.count }}</div>
        <div class="text-xs text-[var(--text-muted)] mt-1">盈 {{ stats.wins }} / 亏 {{ stats.losses }}</div>
      </div>
      <div class="card">
        <div class="text-xs text-[var(--text-muted)]">胜率</div>
        <div class="text-2xl font-semibold mt-1 text-emerald-400">{{ stats.count ? stats.winRate.toFixed(1) : '—' }}%</div>
      </div>
      <div class="card">
        <div class="text-xs text-[var(--text-muted)]">平均盈/亏</div>
        <div class="text-lg font-semibold mt-1">
          <span class="text-emerald-400">{{ stats.count ? fmtNum(stats.avgWin) : '—' }}%</span>
          <span class="text-[var(--text-muted)]"> / </span>
          <span class="text-rose-400">{{ stats.count ? fmtNum(stats.avgLoss) : '—' }}%</span>
        </div>
      </div>
      <div class="card">
        <div class="text-xs text-[var(--text-muted)]">净盈亏</div>
        <div class="text-2xl font-semibold mt-1" :class="pctClass(stats.net)">{{ stats.count ? fmtNum(stats.net) : '—' }}%</div>
        <div class="text-xs text-[var(--text-muted)] mt-1">手续费合计 {{ fmtNum(stats.fees) }}%</div>
      </div>
    </div>

    <!-- 过滤条 -->
    <div class="card flex flex-wrap items-center gap-2 text-sm">
      <span class="text-[var(--text-muted)] text-xs">来源：</span>
      <button v-for="s in (['all','dsl','manual','external','reconcile','ai'] as const)" :key="'src-'+s"
        class="px-3 py-1 rounded-full text-xs border transition-colors"
        :class="sourceFilter === s ? 'bg-violet-500/15 text-violet-300 border-violet-500/40' : 'text-[var(--text-muted)] border-[var(--border)] hover:bg-[var(--surface-hover)]'"
        @click="sourceFilter = s">{{ sourceLabel(s) }}</button>
      <span class="text-[var(--border)] mx-1">|</span>
      <span class="text-[var(--text-muted)] text-xs">方向：</span>
      <button v-for="s in (['all','long','short'] as const)" :key="'side-'+s"
        class="px-3 py-1 rounded-full text-xs border transition-colors"
        :class="sideFilter === s ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40' : 'text-[var(--text-muted)] border-[var(--border)] hover:bg-[var(--surface-hover)]'"
        @click="sideFilter = s">{{ s === 'all' ? '全部' : s === 'long' ? '做多' : '做空' }}</button>
      <span class="text-[var(--border)] mx-1">|</span>
      <span class="text-[var(--text-muted)] text-xs">盈亏：</span>
      <button v-for="s in (['all','win','loss'] as const)" :key="'out-'+s"
        class="px-3 py-1 rounded-full text-xs border transition-colors"
        :class="outcomeFilter === s ? 'bg-amber-500/15 text-amber-300 border-amber-500/40' : 'text-[var(--text-muted)] border-[var(--border)] hover:bg-[var(--surface-hover)]'"
        @click="outcomeFilter = s">{{ s === 'all' ? '全部' : s === 'win' ? '盈利' : '亏损' }}</button>
    </div>

    <div v-if="loading" class="card text-center py-10 text-[var(--text-muted)]">加载中...</div>
    <div v-else-if="error" class="card text-rose-300">⚠️ {{ error }}</div>

    <div v-else class="card overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="text-left text-[var(--text-muted)] border-b border-[var(--border)]">
            <th class="py-2 px-3 whitespace-nowrap">时间</th>
            <th class="py-2 px-3">币种</th>
            <th class="py-2 px-3">方向</th>
            <th class="py-2 px-3 text-right">杠杆</th>
            <th class="py-2 px-3 text-right">开仓价</th>
            <th class="py-2 px-3 text-right">成交价</th>
            <th class="py-2 px-3 text-right">现货%</th>
            <th class="py-2 px-3 text-right">盈亏%</th>
            <th class="py-2 px-3 text-right">手续费%</th>
            <th class="py-2 px-3">平仓原因</th>
            <th class="py-2 px-3 text-center">来源</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in paged" :key="`${t.ts}-${t.coin}`" class="border-b border-[var(--border)] last:border-0 hover:bg-[var(--surface-hover)]">
            <td class="py-2 px-3 text-xs whitespace-nowrap text-[var(--text-muted)]">{{ fmtTime(t.ts) }}</td>
            <td class="py-2 px-3 font-mono font-medium">{{ t.coin }}</td>
            <td class="py-2 px-3">
              <span class="badge" :class="t.side === 'long' ? 'badge-ok' : 'badge-danger'">{{ t.side === 'long' ? '做多' : '做空' }}</span>
            </td>
            <td class="py-2 px-3 text-right font-mono text-[var(--text-muted)]">{{ t.leverage }}x<span v-if="t.leverage_estimated" class="text-[var(--text-muted)] opacity-60">~</span></td>
            <td class="py-2 px-3 text-right font-mono">{{ t.entry_px ? fmtNum(t.entry_px, 5) : '—' }}</td>
            <td class="py-2 px-3 text-right font-mono">{{ t.fill_px ? fmtNum(t.fill_px, 5) : '—' }}</td>
            <td class="py-2 px-3 text-right font-mono" :class="pctClass(t.spot_pct)">{{ t.spot_pct !== undefined ? fmtNum(t.spot_pct) : '—' }}</td>
            <td class="py-2 px-3 text-right font-mono font-semibold" :class="pctClass(t.pnl_pct)">
              {{ fmtNum(t.pnl_pct) }}
              <span v-if="t.pnl_source" class="ml-1 text-[10px] font-normal opacity-70" :title="t.pnl_source === 'fill' ? '按真实成交价' : '按预交易中间价估算'">{{ t.pnl_source === 'fill' ? '成交' : '估算' }}</span>
            </td>
            <td class="py-2 px-3 text-right font-mono text-[var(--text-muted)]">{{ t.fees_pct !== undefined ? fmtNum(t.fees_pct) : '—' }}</td>
            <td class="py-2 px-3 text-xs max-w-48 truncate" :title="t.reason || ''">{{ reasonText(t.reason) }}</td>
            <td class="py-2 px-3 text-center">
              <span class="badge" :class="sourceBadgeClass(t.source)">{{ sourceLabel(t.source) }}</span>
            </td>
          </tr>
          <tr v-if="paged.length === 0">
            <td colspan="11" class="py-10 text-center text-[var(--text-muted)]">暂无平仓交易记录</td>
          </tr>
        </tbody>
      </table>
      <div class="flex items-center justify-between p-3 text-sm">
        <span class="text-[var(--text-muted)]">共 {{ filtered.length }} 条</span>
        <div class="flex gap-2">
          <button class="btn text-xs" :disabled="page <= 1" @click="page--">上一页</button>
          <span class="text-xs text-[var(--text-muted)] self-center">{{ page }} / {{ Math.max(1, Math.ceil(filtered.length / pageSize)) }}</span>
          <button class="btn text-xs" :disabled="page * pageSize >= filtered.length" @click="page++">下一页</button>
        </div>
      </div>
    </div>
  </div>
</template>
