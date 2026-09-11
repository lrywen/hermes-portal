<script setup lang="ts">
/**
 * 成交历史（开仓 + 平仓统一时间线）
 * - 数据来源：hermes-trader /api/dashboard/trades（经 BFF 代理）
 *   close 行：含 PnL%/美元已实现盈亏/杠杆/手续费/平仓原因/成交机制
 *   open  行：execute 真实成交（executed=true），含开仓价/名义价值/regime/止损止盈
 *   开平配对：pair_id 关联两腿，close 带 open_ts/hold_minutes，open 带 close_ts
 * - 统计（仅平仓行）：笔数、胜率、平均盈亏、净盈亏（% 与 $）
 * - 过滤：类型(开/平)、来源(dsl/manual/external/reconcile/ai)、多空、盈亏、币种
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
const kindFilter = ref<'all' | 'open' | 'close'>('all');
const sourceFilter = ref<'all' | 'dsl' | 'manual' | 'external' | 'reconcile' | 'ai'>('all');
const sideFilter = ref<'all' | 'long' | 'short'>('all');
const outcomeFilter = ref<'all' | 'win' | 'loss'>('all');
const page = ref(1);
const pageSize = 20;
let timer: number | null = null;
let sseUnsub: (() => void) | null = null;
let pendingRefresh: number | null = null;

// SSE 事件驱动刷新：开/平仓相关事件或 position_update 到达时防抖拉取。
const REFRESH_EVENTS = new Set(['dsl_exit', 'ai_close', 'close_position', 'hard_killswitch', 'position_update', 'ws_user_fill', 'execute']);
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
  if (kindFilter.value !== 'all') list = list.filter((t) => (t.kind || 'close') === kindFilter.value);
  if (sourceFilter.value !== 'all') list = list.filter((t) => t.kind === 'open' || t.source === sourceFilter.value);
  if (sideFilter.value !== 'all') list = list.filter((t) => t.side === sideFilter.value);
  if (outcomeFilter.value !== 'all') {
    // 开仓行没有盈亏，选盈/亏时只看平仓行
    list = list.filter((t) => t.kind === 'close' && (t.pnl_pct >= 0) === (outcomeFilter.value === 'win'));
  }
  return list;
});

// 统计口径：仅已平仓行
const closes = computed(() => filtered.value.filter((t) => t.kind !== 'open'));
const stats = computed(() => {
  const list = closes.value;
  const wins = list.filter((t) => t.pnl_pct > 0);
  const losses = list.filter((t) => t.pnl_pct < 0);
  const net = list.reduce((s, t) => s + (t.pnl_pct || 0), 0);
  const netUsd = list.reduce((s, t) => s + (typeof t.pnl_usd === 'number' ? t.pnl_usd : 0), 0);
  const usdKnown = list.some((t) => typeof t.pnl_usd === 'number');
  const avgWin = wins.length ? wins.reduce((s, t) => s + t.pnl_pct, 0) / wins.length : 0;
  const avgLoss = losses.length ? losses.reduce((s, t) => s + t.pnl_pct, 0) / losses.length : 0;
  const fees = list.reduce((s, t) => s + (t.fees_pct || 0), 0);
  return {
    count: list.length,
    winRate: list.length ? (wins.length / list.length) * 100 : 0,
    net,
    netUsd,
    usdKnown,
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
    const { data } = await http.get('/api/portal/trader/api/dashboard/trades', {
      params: { limit: limit.value },
    });
    const incoming: any[] = Array.isArray(data) ? data : (data?.trades || []);
    // kind+ts+coin+pair 作为稳定身份做增量合并，保留未变化行的对象引用以减少 Vue patch
    const keyOf = (t: any) => `${t.kind || 'close'}-${t.ts}-${t.coin}-${t.side}-${t.pair_id || ''}`;
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
    error.value = e?.response?.data?.detail || '加载成交历史失败';
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
function fmtUsd(v: any) {
  if (v === null || v === undefined || isNaN(Number(v))) return '—';
  const n = Number(v);
  const d = Math.abs(n) >= 100 ? 2 : 4;
  return (n < 0 ? '-$' : '$') + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}
function fmtHold(mins: any) {
  if (mins === null || mins === undefined || isNaN(Number(mins))) return '—';
  const m = Number(mins);
  if (m < 60) return `${Math.round(m)}分`;
  const h = Math.floor(m / 60);
  const rem = Math.round(m % 60);
  return rem ? `${h}时${rem}分` : `${h}时`;
}
function pctClass(v: any) {
  if (v === null || v === undefined || isNaN(Number(v))) return '';
  return Number(v) > 0 ? 'text-emerald-400' : Number(v) < 0 ? 'text-rose-400' : '';
}
// 策略层平仓原因（为什么平）
function reasonText(r: string) {
  if (!r) return '—';
  const map: Record<string, string> = {
    trailing_stop: '移动止损',
    max_loss: '最大亏损止损',
    take_profit: '止盈',
    hard_timeout: '超时强平',
    stale_flat_timeout: '盘口静止平仓',
    signal_reverse: '信号反转',
    exchange_trigger: '交易所触发止损/止盈',
  };
  for (const [k, v] of Object.entries(map)) {
    if (r.startsWith(k)) return v;
  }
  return r;
}
// 成交机制（怎么成交的）
const MECHANISM_LABELS: Record<string, string> = {
  dsl_market: '策略市价平仓',
  exchange_trigger: '交易所挂单触发',
  manual: '手动平仓',
  ai_market: 'AI 市价平仓',
};
function mechanismText(m: string) {
  return MECHANISM_LABELS[m] || m;
}
function pnlSourceText(s: string) {
  if (s === 'fill') return '实测成交';
  if (s === 'fill_usd') return '实测美元';
  if (s === 'estimated') return '估算';
  if (s === 'unknown') return '未知';
  return s;
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
// 开仓行 hover 备注
function openHint(t: any) {
  const parts: string[] = [];
  if (t.regime) parts.push(`regime: ${t.regime}`);
  if (t.funding_regime) parts.push(`funding: ${t.funding_regime}`);
  if (t.counter_regime) parts.push(`counter: ${t.counter_regime}`);
  if (t.gates) parts.push(`gates: ${JSON.stringify(t.gates)}`);
  if (t.order_id) parts.push(`订单: ${t.order_id}`);
  return parts.join(' · ');
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
  // SSE 事件驱动：成交事件到达时立即刷新（防抖 500ms）
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
        <h2 class="text-xl font-semibold">成交历史</h2>
        <p class="text-sm text-[var(--text-muted)] mt-1">开仓 / 平仓统一时间线，同一笔往返交易自动配对（含持仓时长、已实现盈亏）</p>
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

    <!-- 统计卡片（仅平仓口径） -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div class="card">
        <div class="text-xs text-[var(--text-muted)]">平仓笔数</div>
        <div class="text-2xl font-semibold mt-1">{{ stats.count }}</div>
        <div class="text-xs text-[var(--text-muted)] mt-1">盈 {{ stats.wins }} / 亏 {{ stats.losses }}</div>
      </div>
      <div class="card">
        <div class="text-xs text-[var(--text-muted)]">胜率</div>
        <div class="text-2xl font-semibold mt-1 text-emerald-400">{{ stats.count ? stats.winRate.toFixed(1) : '—' }}%</div>
      </div>
      <div class="card">
        <div class="text-xs text-[var(--text-muted)]" title="按「仓位盈亏%」（含杠杆、已扣手续费的单笔仓位回报率）算术平均">平均盈/亏（仓位盈亏%）</div>
        <div class="text-lg font-semibold mt-1">
          <span class="text-emerald-400">{{ stats.count ? fmtNum(stats.avgWin) : '—' }}%</span>
          <span class="text-[var(--text-muted)]"> / </span>
          <span class="text-rose-400">{{ stats.count ? fmtNum(stats.avgLoss) : '—' }}%</span>
        </div>
      </div>
      <div class="card">
        <div class="text-xs text-[var(--text-muted)]">累计已实现盈亏</div>
        <div class="text-2xl font-semibold mt-1" :class="pctClass(stats.netUsd || stats.net)">
          {{ stats.usdKnown ? fmtUsd(stats.netUsd) : (stats.count ? fmtNum(stats.net) + '%' : '—') }}
        </div>
        <div class="text-xs text-[var(--text-muted)] mt-1" title="手续费百分比为含杠杆口径（吃单费率 × 开平笔数 × 杠杆），占仓位回报的百分比合计">
          {{ stats.usdKnown ? fmtNum(stats.net) + '% · ' : '' }}手续费合计 {{ fmtNum(stats.fees) }}%
        </div>
      </div>
    </div>

    <!-- 过滤条 -->
    <div class="card flex flex-wrap items-center gap-2 text-sm">
      <span class="text-[var(--text-muted)] text-xs">类型：</span>
      <button v-for="s in (['all','open','close'] as const)" :key="'kind-'+s"
        class="px-3 py-1 rounded-full text-xs border transition-colors"
        :class="kindFilter === s ? 'bg-sky-500/15 text-sky-300 border-sky-500/40' : 'text-[var(--text-muted)] border-[var(--border)] hover:bg-[var(--surface-hover)]'"
        @click="kindFilter = s">{{ s === 'all' ? '全部' : s === 'open' ? '开仓' : '平仓' }}</button>
      <span class="text-[var(--border)] mx-1">|</span>
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
            <th class="py-2 px-3 text-center">类型</th>
            <th class="py-2 px-3">币种</th>
            <th class="py-2 px-3">方向</th>
            <th class="py-2 px-3 text-right">杠杆</th>
            <th class="py-2 px-3 text-right">价格</th>
            <th class="py-2 px-3 text-right" title="标的现货本身的价格涨跌幅，未乘杠杆（已扣除手续费前）；仓位盈亏% = 现货% × 杠杆 - 手续费">
              价格涨跌%<span class="opacity-60">ⓘ</span>
            </th>
            <th class="py-2 px-3 text-right" title="含杠杆的已实现仓位回报率（已扣手续费），非标的价格涨跌幅">
              仓位盈亏%<span class="opacity-60">ⓘ</span>
            </th>
            <th class="py-2 px-3 text-right">已实现盈亏</th>
            <th class="py-2 px-3 text-right" title="上行美元金额；下行百分比为含杠杆的手续费占仓位回报口径">手续费<span class="opacity-60">ⓘ</span></th>
            <th class="py-2 px-3">原因 / 备注</th>
            <th class="py-2 px-3 whitespace-nowrap">持仓时长</th>
            <th class="py-2 px-3 text-center">来源</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in paged" :key="`${t.kind || 'close'}-${t.ts}-${t.coin}-${t.side}-${t.pair_id || ''}`"
              class="border-b border-[var(--border)] last:border-0 hover:bg-[var(--surface-hover)] align-top"
              :class="t.kind === 'open' ? 'opacity-90' : ''">
            <td class="py-2 px-3 text-xs whitespace-nowrap text-[var(--text-muted)]">{{ fmtTime(t.ts) }}</td>
            <td class="py-2 px-3 text-center">
              <span v-if="t.kind === 'open'" class="badge badge-info">开仓</span>
              <span v-else class="badge" :class="t.pnl_pct >= 0 ? 'badge-ok' : 'badge-danger'">平仓</span>
            </td>
            <td class="py-2 px-3 font-mono font-medium">
              {{ t.coin }}
              <span v-if="t.pair_id" class="block text-[10px] text-[var(--text-muted)] font-normal">{{ t.pair_id }}</span>
            </td>
            <td class="py-2 px-3">
              <span class="badge" :class="t.side === 'long' ? 'badge-ok' : 'badge-danger'">{{ t.side === 'long' ? '做多' : '做空' }}</span>
            </td>
            <td class="py-2 px-3 text-right font-mono text-[var(--text-muted)]">
              <template v-if="t.kind === 'open'">—</template>
              <template v-else>{{ t.leverage }}x<span v-if="t.leverage_estimated" class="opacity-60">~</span></template>
            </td>
            <td class="py-2 px-3 text-right font-mono">
              <template v-if="t.kind === 'open'">
                <div>{{ t.entry_px ? fmtNum(t.entry_px, 5) : '—' }}</div>
                <div class="text-[10px] text-[var(--text-muted)]">{{ fmtUsd(t.notional_usd) }}</div>
              </template>
              <template v-else>
                <div class="text-[var(--text-muted)] text-[11px]" :title="`开仓价 ${t.entry_px ?? '—'}`">开 {{ t.entry_px ? fmtNum(t.entry_px, 5) : '—' }}</div>
                <div :title="`平仓成交价 ${t.fill_px ?? '—'}`">平 {{ t.fill_px ? fmtNum(t.fill_px, 5) : '—' }}</div>
              </template>
            </td>
            <!-- 现货价格涨跌%（未加杠杆） -->
            <td class="py-2 px-3 text-right font-mono text-[var(--text-muted)]">{{ t.kind === 'close' && t.spot_pct !== undefined && t.spot_pct !== null ? fmtNum(t.spot_pct) : '—' }}</td>
            <!-- 仓位盈亏%（含杠杆、净手续费） -->
            <td class="py-2 px-3 text-right font-mono font-semibold" :class="pctClass(t.pnl_pct)">
              <template v-if="t.kind === 'close'">
                {{ fmtNum(t.pnl_pct) }}
                <span v-if="t.pnl_source" class="ml-1 text-[10px] font-normal opacity-70"
                      :title="pnlSourceText(t.pnl_source)">{{ pnlSourceText(t.pnl_source) }}</span>
              </template>
              <template v-else>—</template>
            </td>
            <!-- 单笔已实现盈亏（美元） -->
            <td class="py-2 px-3 text-right font-mono font-semibold" :class="pctClass(t.pnl_usd)">
              <template v-if="t.kind === 'close'">
                <div v-if="t.pnl_usd !== null && t.pnl_usd !== undefined">{{ fmtUsd(t.pnl_usd) }}</div>
                <span v-else class="text-[10px] font-normal text-[var(--text-muted)]" title="该来源未记录美元成交金额">未记录</span>
                <div v-if="t.gross_pnl_usd !== null && t.gross_pnl_usd !== undefined" class="text-[10px] font-normal opacity-70">毛 {{ fmtUsd(t.gross_pnl_usd) }}</div>
              </template>
              <template v-else>—</template>
            </td>
            <!-- 手续费 -->
            <td class="py-2 px-3 text-right font-mono text-[var(--text-muted)]">
              <template v-if="t.kind === 'close'">
                <div v-if="t.fee_usd !== null && t.fee_usd !== undefined">{{ fmtUsd(t.fee_usd) }}</div>
                <div>{{ t.fees_pct !== undefined && t.fees_pct !== null ? fmtNum(t.fees_pct) + '%' : '—' }}</div>
              </template>
              <template v-else>—</template>
            </td>
            <!-- 原因 / 备注 -->
            <td class="py-2 px-3 text-xs max-w-56">
              <template v-if="t.kind === 'open'">
                <div class="truncate" :title="openHint(t)">
                  <span v-if="t.regime" class="badge badge-muted mr-1">{{ t.regime }}</span>
                  <span v-if="t.funding_regime" class="badge badge-muted mr-1">{{ t.funding_regime }}</span>
                  <span v-if="t.sl_missing" class="badge badge-warn mr-1" title="成交时未挂止损">无止损</span>
                  <span v-if="t.bracket_error" class="badge badge-danger mr-1" :title="String(t.bracket_error)"> brackets 异常</span>
                </div>
                <div v-if="t.stop_px || t.tp_px" class="text-[10px] text-[var(--text-muted)] mt-1">
                  止损 {{ t.stop_px ? fmtNum(t.stop_px, 5) : '—' }} / 止盈 {{ t.tp_px ? fmtNum(t.tp_px, 5) : '—' }}
                </div>
              </template>
              <template v-else>
                <div class="truncate" :title="t.detail || t.reason || ''">
                  {{ t.reason ? reasonText(t.reason) : (t.exit_mechanism ? mechanismText(t.exit_mechanism) : '—') }}
                </div>
                <div class="text-[10px] text-[var(--text-muted)] mt-0.5 truncate">
                  <template v-if="t.exit_mechanism">{{ mechanismText(t.exit_mechanism) }}</template>
                  <template v-if="t.entry_regime"><span v-if="t.exit_mechanism"> · </span>入场 {{ t.entry_regime }}</template>
                </div>
              </template>
            </td>
            <!-- 持仓时长 -->
            <td class="py-2 px-3 text-xs whitespace-nowrap text-[var(--text-muted)]">
              <template v-if="t.kind === 'close'">{{ fmtHold(t.hold_minutes) }}</template>
              <template v-else>
                <span v-if="t.close_ts" class="text-emerald-400/80">已平仓</span>
                <span v-else class="text-amber-400/80">持仓中</span>
              </template>
            </td>
            <td class="py-2 px-3 text-center">
              <span v-if="t.kind !== 'open'" class="badge" :class="sourceBadgeClass(t.source)">{{ sourceLabel(t.source) }}</span>
              <span v-else class="badge badge-muted">成交</span>
            </td>
          </tr>
          <tr v-if="paged.length === 0">
            <td colspan="13" class="py-10 text-center text-[var(--text-muted)]">暂无成交记录</td>
          </tr>
        </tbody>
      </table>
      <div class="flex items-center justify-between p-3 text-sm">
        <span class="text-[var(--text-muted)]">共 {{ filtered.length }} 条（开仓 {{ filtered.filter((t:any) => t.kind === 'open').length }} / 平仓 {{ closes.length }}）</span>
        <div class="flex gap-2">
          <button class="btn text-xs" :disabled="page <= 1" @click="page--">上一页</button>
          <span class="text-xs text-[var(--text-muted)] self-center">{{ page }} / {{ Math.max(1, Math.ceil(filtered.length / pageSize)) }}</span>
          <button class="btn text-xs" :disabled="page * pageSize >= filtered.length" @click="page++">下一页</button>
        </div>
      </div>
    </div>
  </div>
</template>
