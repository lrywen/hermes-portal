<script setup lang="ts">
/**
 * 影子账本（SHADOW 模拟盘）
 * - 数据来源：hermes-trader /api/dashboard/shadow/*（经 BFF 代理）
 * - 展示：模拟账户资金（权益/钱包/可用/保证金/浮动盈亏）、持仓、开平仓流水、
 *   权益曲线、盈亏统计、资金变动明细
 * - 写操作（shadow:manage）：重置账本（可设起始资金）、手动模拟平仓
 * - 刷新：10s 轮询 + SSE 事件（shadow_exit / position_update）防抖触发
 */
import { computed, onMounted, onUnmounted, ref, shallowRef } from 'vue';
import http from '@/shared/api/client';
import { useAuthStore } from '@/stores/auth';
import { useToast } from '@/stores/toast';
import { useSseFeedStore } from '@/stores/sseFeed';
import VChart from 'vue-echarts';
import { use } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, MarkLineComponent } from 'echarts/components';

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, MarkLineComponent]);

const API = '/api/portal/trader/api/dashboard/shadow';

const auth = useAuthStore();
const toast = useToast();

const loading = ref(true);
const error = ref('');
const account = ref<any>(null);
const stats = ref<any>(null);
const fills = ref<any[]>([]);
const curve = ref<any[]>([]);
const equityOption = shallowRef<any>({});

const typeFilter = ref<'all' | 'open' | 'close'>('all');
const sideFilter = ref<'all' | 'long' | 'short'>('all');
const filterCoin = ref('');
const resetBalance = ref<string>('');
const closing = ref<string | null>(null);
const resetting = ref(false);
const depositAmount = ref<string>('');
const depositing = ref(false);

let timer: number | null = null;
let sseUnsub: (() => void) | null = null;
let pendingRefresh: number | null = null;

const canManage = computed(() => auth.hasPermission('shadow:manage'));

const REFRESH_EVENTS = new Set(['shadow_exit', 'shadow_open', 'position_update']);
function scheduleRefresh() {
  if (pendingRefresh !== null) return;
  pendingRefresh = window.setTimeout(() => {
    pendingRefresh = null;
    loadAll();
  }, 500);
}

// ---------------------------------------------------------------- data
async function loadAll() {
  try {
    error.value = '';
    const [a, s, t, e] = await Promise.all([
      http.get(`${API}/account`),
      http.get(`${API}/stats`),
      http.get(`${API}/trades`, { params: { limit: 500 } }),
      http.get(`${API}/equity-curve`),
    ]);
    account.value = a.data;
    stats.value = s.data;
    fills.value = Array.isArray(t.data?.trades) ? t.data.trades : [];
    curve.value = Array.isArray(e.data?.points) ? e.data.points : [];
    if (!resetBalance.value && a.data?.starting_balance) {
      resetBalance.value = String(a.data.starting_balance);
    }
    buildChart();
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '加载影子账本失败';
  } finally {
    loading.value = false;
  }
}

function buildChart() {
  const start = Number(stats.value?.starting_balance ?? account.value?.starting_balance ?? 0);
  const seriesData = curve.value.map((d: any) => {
    const ts = typeof d.ts === 'number' ? (d.ts < 1e12 ? d.ts * 1000 : d.ts) : Date.now();
    return [ts, Number(Number(d.equity ?? 0).toFixed(2))] as [number, number];
  });
  equityOption.value = {
    backgroundColor: 'transparent',
    grid: { left: 64, right: 24, top: 24, bottom: 32 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#0B0E14',
      borderColor: '#232A3B',
      textStyle: { color: '#E5E7EB', fontSize: 11 },
      formatter: (params: any) => {
        const p = params[0];
        if (!p) return '';
        const time = new Date(p.data[0]).toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
        return `${time}<br/>模拟权益: <strong>$${Number(p.data[1]).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong>`;
      },
    },
    xAxis: {
      type: 'time',
      axisLine: { lineStyle: { color: '#232A3B' } },
      axisLabel: { color: '#6B7280', fontSize: 10 },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      scale: true,
      splitLine: { lineStyle: { color: '#1E2433' } },
      axisLabel: { color: '#6B7280', fontSize: 10, formatter: (v: number) => `$${v.toFixed(0)}` },
    },
    series: [
      {
        type: 'line',
        data: seriesData,
        smooth: true,
        showSymbol: false,
        lineStyle: { color: '#8B5CF6', width: 2 },
        areaStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(139,92,246,0.20)' },
              { offset: 1, color: 'rgba(139,92,246,0)' },
            ],
          },
        },
        markLine: start > 0 ? {
          silent: true,
          symbol: 'none',
          lineStyle: { color: '#6B7280', type: 'dashed', width: 1 },
          label: { color: '#9CA3AF', fontSize: 10, formatter: `起始 $${start.toLocaleString()}` },
          data: [{ yAxis: start }],
        } : undefined,
      },
    ],
  };
}

// ---------------------------------------------------------------- derived
const positions = computed<any[]>(() => account.value?.positions || []);

const filteredFills = computed(() => {
  let list = fills.value;
  if (typeFilter.value !== 'all') list = list.filter((f) => f.type === typeFilter.value);
  if (sideFilter.value !== 'all') list = list.filter((f) => f.side === sideFilter.value);
  if (filterCoin.value) {
    const q = filterCoin.value.toUpperCase();
    list = list.filter((f) => (f.coin || '').toUpperCase().includes(q));
  }
  return list;
});

// 资金变动明细：按时间正序累计已实现盈亏，得到每次平仓后的钱包余额
const fundFlow = computed(() => {
  const start = Number(account.value?.starting_balance ?? stats.value?.starting_balance ?? 0);
  const closes = fills.value
    .filter((f) => f.type === 'close')
    .slice()
    .sort((a, b) => Number(a.ts) - Number(b.ts));
  let running = start;
  const rows = closes.map((f) => {
    const pnl = Number(f.realized_pnl_usd || 0);
    running += pnl;
    return { ...f, balance_after: running };
  });
  return { start, rows: rows.reverse() };
});

// ---------------------------------------------------------------- actions
async function manualClose(p: any) {
  if (!canManage.value) return;
  if (!confirm(`确定按当前标记价模拟平仓 ${p.coin}（${p.side === 'long' ? '做多' : '做空'}）？`)) return;
  closing.value = p.coin;
  try {
    const { data } = await http.post(`${API}/close`, { coin: p.coin, side: p.side });
    const f = data?.fill;
    toast.ok(`${p.coin} 已模拟平仓，盈亏 $${Number(f?.realized_pnl_usd || 0).toFixed(2)}`);
    await loadAll();
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '模拟平仓失败');
  } finally {
    closing.value = null;
  }
}

async function resetBook() {
  if (!canManage.value) return;
  const bal = parseFloat(resetBalance.value);
  if (isNaN(bal) || bal <= 0) {
    toast.err('请输入有效的起始资金');
    return;
  }
  if (!confirm(`重置将清空全部模拟持仓与流水，并把起始资金设为 $${bal.toLocaleString()}，确定？`)) return;
  resetting.value = true;
  try {
    await http.post(`${API}/reset`, { starting_balance: bal });
    toast.ok('影子账本已重置');
    await loadAll();
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '重置失败');
  } finally {
    resetting.value = false;
  }
}

async function depositFunds() {
  if (!canManage.value) return;
  const amt = parseFloat(depositAmount.value);
  if (isNaN(amt) || amt <= 0) {
    toast.err('请输入有效的注资金额');
    return;
  }
  if (!confirm(`向影子账户追加 $${amt.toLocaleString()} 虚拟资金？\n（持仓、流水、盈亏统计均保留，仅增加可用资金）`)) return;
  depositing.value = true;
  try {
    const { data } = await http.post(`${API}/deposit`, { amount: amt });
    toast.ok(`已注资 $${amt.toLocaleString()}，可用资金 $${Number(data?.available || 0).toLocaleString()}`);
    depositAmount.value = '';
    await loadAll();
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '注资失败');
  } finally {
    depositing.value = false;
  }
}

// ---------------------------------------------------------------- formatters
function fmtUsd(v: any, d = 2) {
  if (v === null || v === undefined || isNaN(Number(v))) return '—';
  return '$' + Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}
function fmtNum(v: any, d = 2) {
  if (v === null || v === undefined || isNaN(Number(v))) return '—';
  return Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}
function fmtPct(v: any, d = 2) {
  if (v === null || v === undefined || isNaN(Number(v))) return '—';
  const n = Number(v);
  return (n >= 0 ? '+' : '') + n.toFixed(d) + '%';
}
function pnlColor(v: any) {
  if (v === null || v === undefined || isNaN(Number(v))) return 'text-[var(--text-muted)]';
  if (Number(v) > 0) return 'text-emerald-400';
  if (Number(v) < 0) return 'text-rose-400';
  return '';
}
function fmtTime(t: any) {
  if (!t) return '—';
  const d = new Date(typeof t === 'number' ? (t < 1e12 ? t * 1000 : t) : t);
  return isNaN(d.getTime()) ? String(t) : d.toLocaleString('zh-CN', { hour12: false });
}
function holdText(openedAt: any) {
  if (!openedAt) return '—';
  const ts = typeof openedAt === 'number' ? (openedAt < 1e12 ? openedAt * 1000 : openedAt) : 0;
  const min = Math.max(0, (Date.now() - ts) / 60000);
  if (min < 60) return `${min.toFixed(0)} 分钟`;
  const h = min / 60;
  if (h < 24) return `${h.toFixed(1)} 小时`;
  return `${(h / 24).toFixed(1)} 天`;
}
function holdMinText(m: any) {
  const min = Number(m);
  if (isNaN(min) || min <= 0) return '—';
  if (min < 60) return `${min.toFixed(0)} 分钟`;
  const h = min / 60;
  if (h < 24) return `${h.toFixed(1)} 小时`;
  return `${(h / 24).toFixed(1)} 天`;
}
function reasonText(r: string) {
  const map: Record<string, string> = {
    max_loss: '止损', take_profit: '止盈', hard_timeout: '超时强平',
    stale_flat: '盘口静止', signal_reverse: '信号反转', manual_close: '手动平仓',
    dsl_exit: 'DSL 退出',
  };
  if (!r) return '—';
  for (const [k, v] of Object.entries(map)) if (r.startsWith(k)) return v;
  return r;
}

onMounted(() => {
  loadAll();
  const sse = useSseFeedStore();
  sseUnsub = sse.onEvent((data) => {
    if (data?.event && REFRESH_EVENTS.has(data.event)) scheduleRefresh();
  });
  timer = window.setInterval(loadAll, 10000);
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
        <h2 class="text-xl font-semibold flex items-center gap-2">
          影子账本
          <span class="badge badge-warn">SHADOW 模拟盘</span>
          <span v-if="account && !account.enabled" class="badge badge-danger">账本未启用</span>
        </h2>
        <p class="text-sm text-[var(--text-muted)] mt-1">
          SHADOW 模式下策略「本会执行」的模拟成交与盈亏 · 10s 自动盯市刷新
        </p>
      </div>
      <div class="flex gap-2 items-center flex-wrap">
        <button class="btn" @click="loadAll">🔄 刷新</button>
        <div v-if="canManage" class="flex items-center gap-1 card !py-1.5 !px-2">
          <span class="text-xs text-[var(--text-muted)]">追加资金</span>
          <input class="input !py-1" v-model="depositAmount" style="width: 100px" placeholder="金额" />
          <button class="btn text-xs" style="background:#16a34a;color:#fff" :disabled="depositing" @click="depositFunds">
            {{ depositing ? '注资中...' : '💰 注资' }}
          </button>
        </div>
        <div v-if="canManage" class="flex items-center gap-1 card !py-1.5 !px-2">
          <span class="text-xs text-[var(--text-muted)]">起始资金</span>
          <input class="input !py-1" v-model="resetBalance" style="width: 110px" placeholder="10000" />
          <button class="btn btn-danger text-xs" :disabled="resetting" @click="resetBook">
            {{ resetting ? '重置中...' : '重置账本' }}
          </button>
        </div>
      </div>
    </header>

    <div v-if="loading" class="card text-center py-10 text-[var(--text-muted)]">加载中...</div>
    <div v-else-if="error" class="card text-rose-300">⚠️ {{ error }}</div>

    <template v-else>
      <!-- 账户资金（仿交易平台资金栏） -->
      <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">账户权益</div>
          <div class="text-xl font-semibold mt-1" :class="pnlColor(stats?.total_return_pct)">
            {{ fmtUsd(account?.equity) }}
          </div>
          <div class="text-xs mt-1" :class="pnlColor(stats?.total_return_pct)">{{ fmtPct(stats?.total_return_pct) }} 总收益</div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">钱包余额</div>
          <div class="text-xl font-semibold mt-1">{{ fmtUsd(account?.wallet_balance) }}</div>
          <div class="text-xs text-[var(--text-muted)] mt-1">起始 {{ fmtUsd(account?.starting_balance, 0) }}</div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">可用资金</div>
          <div class="text-xl font-semibold mt-1 text-sky-400">{{ fmtUsd(account?.available) }}</div>
          <div class="text-xs text-[var(--text-muted)] mt-1">可开仓保证金</div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">占用保证金</div>
          <div class="text-xl font-semibold mt-1 text-amber-300">{{ fmtUsd(account?.used_margin) }}</div>
          <div class="text-xs text-[var(--text-muted)] mt-1">持仓名义 {{ fmtUsd(account?.open_notional_usd, 0) }}</div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">未实现盈亏</div>
          <div class="text-xl font-semibold mt-1" :class="pnlColor(account?.unrealized_pnl_usd)">
            {{ fmtUsd(account?.unrealized_pnl_usd) }}
          </div>
          <div class="text-xs text-[var(--text-muted)] mt-1">{{ account?.open_positions || 0 }} 笔持仓</div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">已实现盈亏</div>
          <div class="text-xl font-semibold mt-1" :class="pnlColor(account?.realized_pnl_usd)">
            {{ fmtUsd(account?.realized_pnl_usd) }}
          </div>
          <div class="text-xs text-[var(--text-muted)] mt-1">手续费 {{ fmtUsd(account?.total_fees_usd) }}</div>
        </div>
      </div>

      <!-- 盈亏统计分析 -->
      <div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3">
        <div class="card"><div class="text-xs text-[var(--text-muted)]">已平仓</div><div class="text-lg font-semibold mt-1">{{ stats?.closed_trades ?? 0 }}</div></div>
        <div class="card"><div class="text-xs text-[var(--text-muted)]">胜率</div><div class="text-lg font-semibold mt-1 text-emerald-400">{{ stats?.closed_trades ? fmtNum(stats.win_rate_pct, 1) : '—' }}%</div><div class="text-[11px] text-[var(--text-muted)]">盈 {{ stats?.wins ?? 0 }} / 亏 {{ stats?.losses ?? 0 }}</div></div>
        <div class="card"><div class="text-xs text-[var(--text-muted)]">盈亏比</div><div class="text-lg font-semibold mt-1 text-violet-300">{{ stats?.profit_factor != null ? fmtNum(stats.profit_factor) : '—' }}</div></div>
        <div class="card"><div class="text-xs text-[var(--text-muted)]">平均盈利</div><div class="text-lg font-semibold mt-1 text-emerald-400">{{ fmtUsd(stats?.avg_win_usd) }}</div></div>
        <div class="card"><div class="text-xs text-[var(--text-muted)]">平均亏损</div><div class="text-lg font-semibold mt-1 text-rose-400">{{ fmtUsd(stats?.avg_loss_usd) }}</div></div>
        <div class="card"><div class="text-xs text-[var(--text-muted)]">最佳/最差</div><div class="text-sm font-semibold mt-1"><span class="text-emerald-400">{{ fmtUsd(stats?.best_trade_usd, 0) }}</span><br /><span class="text-rose-400">{{ fmtUsd(stats?.worst_trade_usd, 0) }}</span></div></div>
        <div class="card"><div class="text-xs text-[var(--text-muted)]">平均持仓</div><div class="text-lg font-semibold mt-1">{{ holdMinText(stats?.avg_hold_minutes) }}</div></div>
        <div class="card"><div class="text-xs text-[var(--text-muted)]">累计手续费</div><div class="text-lg font-semibold mt-1 text-amber-300">{{ fmtUsd(stats?.total_fees_usd) }}</div></div>
      </div>

      <!-- 权益曲线 -->
      <div class="card">
        <div class="text-sm font-medium mb-2">模拟权益曲线</div>
        <div v-if="curve.length < 2" class="text-center py-10 text-[var(--text-muted)] text-sm">
          暂无曲线数据（开仓/平仓后生成）
        </div>
        <v-chart v-else :option="equityOption" style="height: 300px" autoresize />
      </div>

      <!-- 当前模拟持仓 -->
      <div class="card overflow-x-auto">
        <div class="text-sm font-medium mb-2 px-1">当前模拟持仓（{{ positions.length }}）</div>
        <table class="w-full text-xs">
          <thead>
            <tr class="text-left text-[var(--text-muted)] border-b border-[var(--border)]">
              <th class="py-2 px-3 font-medium">币种</th>
              <th class="py-2 px-3 font-medium">方向</th>
              <th class="py-2 px-3 font-medium text-right">名义/数量</th>
              <th class="py-2 px-3 font-medium text-right">杠杆</th>
              <th class="py-2 px-3 font-medium text-right">保证金</th>
              <th class="py-2 px-3 font-medium text-right">开仓价</th>
              <th class="py-2 px-3 font-medium text-right">标记价</th>
              <th class="py-2 px-3 font-medium text-right">现货%</th>
              <th class="py-2 px-3 font-medium text-right">ROE%</th>
              <th class="py-2 px-3 font-medium text-right">浮动盈亏</th>
              <th class="py-2 px-3 font-medium">持仓时长</th>
              <th class="py-2 px-3 font-medium text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in positions" :key="p.id" class="border-b border-[var(--border)] last:border-0 hover:bg-[var(--surface-hover)]">
              <td class="py-2.5 px-3 font-mono font-semibold">{{ p.coin }}</td>
              <td class="py-2.5 px-3">
                <span class="badge" :class="p.side === 'long' ? 'badge-ok' : 'badge-danger'">{{ p.side === 'long' ? '做多' : '做空' }}</span>
              </td>
              <td class="py-2.5 px-3 font-mono text-right">
                <div>{{ fmtUsd(p.size_usd, 0) }}</div>
                <div class="text-[10px] text-[var(--text-muted)]">{{ fmtNum(p.size_coin, 4) }}</div>
              </td>
              <td class="py-2.5 px-3 font-mono text-right text-[var(--text-muted)]">{{ p.leverage }}x</td>
              <td class="py-2.5 px-3 font-mono text-right text-amber-300">{{ fmtUsd(p.size_usd / Math.max(1, p.leverage)) }}</td>
              <td class="py-2.5 px-3 font-mono text-right">{{ fmtNum(p.entry_px, 6) }}</td>
              <td class="py-2.5 px-3 font-mono text-right">{{ fmtNum(p.mark_px, 6) }}</td>
              <td class="py-2.5 px-3 font-mono text-right" :class="pnlColor(p.unrealized_pct)">{{ fmtPct(p.unrealized_pct) }}</td>
              <td class="py-2.5 px-3 font-mono text-right font-semibold" :class="pnlColor(p.unrealized_roe_pct)">{{ fmtPct(p.unrealized_roe_pct) }}</td>
              <td class="py-2.5 px-3 font-mono text-right font-medium" :class="pnlColor(p.unrealized_pnl_usd)">
                {{ Number(p.unrealized_pnl_usd) >= 0 ? '+' : '' }}{{ fmtUsd(p.unrealized_pnl_usd) }}
              </td>
              <td class="py-2.5 px-3 text-[var(--text-muted)] whitespace-nowrap">{{ holdText(p.opened_at) }}</td>
              <td class="py-2.5 px-3 text-right">
                <button class="btn btn-danger text-xs" :disabled="!canManage || closing === p.coin" @click="manualClose(p)">
                  {{ closing === p.coin ? '平仓中...' : '平仓' }}
                </button>
              </td>
            </tr>
            <tr v-if="positions.length === 0">
              <td colspan="12" class="py-8 text-center text-[var(--text-muted)]">
                当前无模拟持仓
                <div class="mt-1 text-xs">SHADOW 模式下策略通过全部风控闸门时，将在此模拟开仓</div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 开平仓流水 -->
      <div class="card overflow-x-auto">
        <div class="flex items-center justify-between flex-wrap gap-2 mb-2 px-1">
          <div class="text-sm font-medium">开平仓流水</div>
          <div class="flex gap-2 items-center flex-wrap text-xs">
            <input class="input !py-1" v-model="filterCoin" placeholder="筛选币种..." style="width: 110px" />
            <button v-for="s in (['all','open','close'] as const)" :key="'t-'+s"
              class="px-3 py-1 rounded-full border transition-colors"
              :class="typeFilter === s ? 'bg-violet-500/15 text-violet-300 border-violet-500/40' : 'text-[var(--text-muted)] border-[var(--border)] hover:bg-[var(--surface-hover)]'"
              @click="typeFilter = s">{{ s === 'all' ? '全部' : s === 'open' ? '开仓' : '平仓' }}</button>
            <button v-for="s in (['all','long','short'] as const)" :key="'s-'+s"
              class="px-3 py-1 rounded-full border transition-colors"
              :class="sideFilter === s ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40' : 'text-[var(--text-muted)] border-[var(--border)] hover:bg-[var(--surface-hover)]'"
              @click="sideFilter = s">{{ s === 'all' ? '全部' : s === 'long' ? '做多' : '做空' }}</button>
          </div>
        </div>
        <table class="w-full text-xs">
          <thead>
            <tr class="text-left text-[var(--text-muted)] border-b border-[var(--border)]">
              <th class="py-2 px-3 font-medium whitespace-nowrap">时间</th>
              <th class="py-2 px-3 font-medium">类型</th>
              <th class="py-2 px-3 font-medium">币种</th>
              <th class="py-2 px-3 font-medium">方向</th>
              <th class="py-2 px-3 font-medium text-right">数量</th>
              <th class="py-2 px-3 font-medium text-right">开仓价</th>
              <th class="py-2 px-3 font-medium text-right">成交价</th>
              <th class="py-2 px-3 font-medium text-right">名义</th>
              <th class="py-2 px-3 font-medium text-right">手续费</th>
              <th class="py-2 px-3 font-medium text-right">盈亏</th>
              <th class="py-2 px-3 font-medium text-right">ROE%</th>
              <th class="py-2 px-3 font-medium">原因/时长</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="f in filteredFills" :key="f.id" class="border-b border-[var(--border)] last:border-0 hover:bg-[var(--surface-hover)]">
              <td class="py-2 px-3 text-[var(--text-muted)] whitespace-nowrap">{{ fmtTime(f.ts) }}</td>
              <td class="py-2 px-3">
                <span class="badge" :class="f.type === 'open' ? 'badge-purple' : 'badge-muted'">{{ f.type === 'open' ? '开仓' : '平仓' }}</span>
              </td>
              <td class="py-2 px-3 font-mono font-medium">{{ f.coin }}</td>
              <td class="py-2 px-3">
                <span class="badge" :class="f.side === 'long' ? 'badge-ok' : 'badge-danger'">{{ f.side === 'long' ? '多' : '空' }}</span>
              </td>
              <td class="py-2 px-3 font-mono text-right">{{ fmtNum(f.qty, 4) }}</td>
              <td class="py-2 px-3 font-mono text-right">{{ f.type === 'close' ? fmtNum(f.entry_px, 6) : '—' }}</td>
              <td class="py-2 px-3 font-mono text-right">{{ fmtNum(f.price, 6) }}</td>
              <td class="py-2 px-3 font-mono text-right">{{ fmtUsd(f.notional_usd, 0) }}</td>
              <td class="py-2 px-3 font-mono text-right text-amber-300">{{ f.type === 'close' ? fmtUsd(f.fee_usd, 3) : '—' }}</td>
              <td class="py-2.5 px-3 font-mono text-right font-medium" :class="f.type === 'close' ? pnlColor(f.realized_pnl_usd) : 'text-[var(--text-muted)]'">
                {{ f.type === 'close' ? (Number(f.realized_pnl_usd) >= 0 ? '+' : '') + fmtUsd(f.realized_pnl_usd) : '—' }}
              </td>
              <td class="py-2 px-3 font-mono text-right" :class="f.type === 'close' ? pnlColor(f.realized_pnl_pct) : 'text-[var(--text-muted)]'">
                {{ f.type === 'close' ? fmtPct(f.realized_pnl_pct) : '—' }}
              </td>
              <td class="py-2 px-3 text-[var(--text-muted)] whitespace-nowrap">
                <template v-if="f.type === 'close'">{{ reasonText(f.reason) }} · {{ holdMinText(f.hold_minutes) }}</template>
                <template v-else>—</template>
              </td>
            </tr>
            <tr v-if="filteredFills.length === 0">
              <td colspan="12" class="py-8 text-center text-[var(--text-muted)]">暂无成交流水</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 资金变动明细 -->
      <div class="card overflow-x-auto">
        <div class="text-sm font-medium mb-2 px-1">账户资金变动明细（平仓已实现盈亏 → 钱包余额）</div>
        <table class="w-full text-xs">
          <thead>
            <tr class="text-left text-[var(--text-muted)] border-b border-[var(--border)]">
              <th class="py-2 px-3 font-medium whitespace-nowrap">时间</th>
              <th class="py-2 px-3 font-medium">币种</th>
              <th class="py-2 px-3 font-medium">方向</th>
              <th class="py-2 px-3 font-medium">事项</th>
              <th class="py-2 px-3 font-medium text-right">现货%</th>
              <th class="py-2 px-3 font-medium text-right">手续费</th>
              <th class="py-2 px-3 font-medium text-right">已实现盈亏</th>
              <th class="py-2 px-3 font-medium text-right">平仓后钱包余额</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in fundFlow.rows" :key="r.id" class="border-b border-[var(--border)] last:border-0 hover:bg-[var(--surface-hover)]">
              <td class="py-2 px-3 text-[var(--text-muted)] whitespace-nowrap">{{ fmtTime(r.ts) }}</td>
              <td class="py-2 px-3 font-mono font-medium">{{ r.coin }}</td>
              <td class="py-2 px-3"><span class="badge" :class="r.side === 'long' ? 'badge-ok' : 'badge-danger'">{{ r.side === 'long' ? '做多' : '做空' }}</span></td>
              <td class="py-2 px-3 text-[var(--text-muted)]">{{ reasonText(r.reason) }}平仓</td>
              <td class="py-2 px-3 font-mono text-right" :class="pnlColor(r.spot_pct)">{{ fmtPct(r.spot_pct) }}</td>
              <td class="py-2 px-3 font-mono text-right text-amber-300">{{ fmtUsd(r.fee_usd, 3) }}</td>
              <td class="py-2 px-3 font-mono text-right font-semibold" :class="pnlColor(r.realized_pnl_usd)">
                {{ Number(r.realized_pnl_usd) >= 0 ? '+' : '' }}{{ fmtUsd(r.realized_pnl_usd) }}
              </td>
              <td class="py-2 px-3 font-mono text-right font-medium">{{ fmtUsd(r.balance_after) }}</td>
            </tr>
            <tr v-if="fundFlow.rows.length === 0">
              <td colspan="8" class="py-8 text-center text-[var(--text-muted)]">
                暂无资金变动（初始资金 {{ fmtUsd(fundFlow.start) }}，平仓后此处记录每笔已实现盈亏与余额）
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
