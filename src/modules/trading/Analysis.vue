<script setup lang="ts">
/**
 * 深度分析（双视图）
 * - 行情：K 线 + 盘口深度 + 价格（hermes-trader /api/hl/*）
 * - 权益：权益曲线 + DEX 分布 + 平仓原因 PnL 统计（hermes-trader /api/dashboard/*）
 */
import { computed, nextTick, onMounted, onUnmounted, ref, shallowRef, watch } from 'vue';
import http from '@/shared/api/client';
import VChart from 'vue-echarts';
import { use } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { CandlestickChart, BarChart, LineChart, PieChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, DataZoomComponent, LegendComponent, GraphicComponent } from 'echarts/components';
import { useToast } from '@/stores/toast';

use([CanvasRenderer, CandlestickChart, BarChart, LineChart, PieChart, GridComponent, TooltipComponent, DataZoomComponent, LegendComponent, GraphicComponent]);

const toast = useToast();
const tab = ref<'market' | 'equity'>('market');

// ============ 行情视图 ============
const coin = ref('BTC');
const coins = ['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE', 'AVAX', 'LINK'];
const interval = ref('1h');
const loading = ref(false);
const error = ref('');
const orderbook = ref<any>(null);
const price = ref<any>(null);
const candleOption = shallowRef<any>({});

async function loadCandle() {
  loading.value = true;
  error.value = '';
  try {
    const { data } = await http.get('/api/portal/trader/api/hl/candles', {
      params: { coin: coin.value, interval: interval.value, limit: 200 },
    });
    const rows = Array.isArray(data) ? data : data?.candles || [];
    const categoryData = rows.map((r: any) => r.t ? new Date(r.t).toLocaleDateString() : r.time);
    const values = rows.map((r: any) => [r.o ?? r.open, r.c ?? r.close, r.l ?? r.low, r.h ?? r.high]);
    const volumes = rows.map((r: any) => ({
      value: r.v ?? r.volume,
      itemStyle: { color: (r.c ?? r.close) >= (r.o ?? r.open) ? '#10b981' : '#f43f5e' },
    }));
    candleOption.value = {
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
      legend: { data: ['K线', '成交量'], textStyle: { color: '#94a3b8' } },
      grid: [{ left: 50, right: 20, top: 30, height: '55%' }, { left: 50, right: 20, top: '72%', height: '18%' }],
      xAxis: [
        { type: 'category', data: categoryData, axisLine: { lineStyle: { color: '#475569' } } },
        { type: 'category', gridIndex: 1, data: categoryData, axisLabel: { show: false } },
      ],
      yAxis: [
        { scale: true, splitLine: { lineStyle: { color: 'rgba(148,163,184,0.15)' } } },
        { gridIndex: 1, splitLine: { show: false } },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start: 60, end: 100 },
        { show: true, xAxisIndex: [0, 1], type: 'slider', top: '93%', start: 60, end: 100 },
      ],
      series: [
        { name: 'K线', type: 'candlestick', data: values, itemStyle: { color: '#10b981', color0: '#f43f5e', borderColor: '#10b981', borderColor0: '#f43f5e' } },
        { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volumes },
      ],
    };
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '加载 K 线失败';
  } finally {
    loading.value = false;
  }
}

async function loadOrderbook() {
  try {
    const { data } = await http.get('/api/portal/trader/api/hl/orderbook', { params: { coin: coin.value } });
    orderbook.value = data;
  } catch { /* 静默 */ }
}

async function loadPrice() {
  try {
    const { data } = await http.get('/api/portal/trader/api/hl/price', { params: { coin: coin.value } });
    price.value = data;
  } catch { /* 静默 */ }
}

function refreshMarket() {
  loadCandle();
  loadOrderbook();
  loadPrice();
}

watch([coin, interval], refreshMarket);

// ============ 权益视图 ============
type RangeKey = '1d' | '7d' | '30d';
const ranges: Record<RangeKey, { label: string; seconds: number }> = {
  '1d': { label: '24小时', seconds: 86400 },
  '7d': { label: '7天', seconds: 604800 },
  '30d': { label: '30天', seconds: 2592000 },
};
const activeRange = ref<RangeKey>('1d');
const curve = ref<{ ts: number; equity: number }[]>([]);
const summary = ref<any>(null);
const closedTrades = ref<any[]>([]);
const equityLoading = ref(false);

const equityOption = shallowRef<any>({});
const dexOption = shallowRef<any>({});
const reasonOption = shallowRef<any>({});

const rangeStats = computed(() => {
  if (!curve.value.length) return null;
  const values = curve.value.map((p) => p.equity);
  const start = values[0];
  const end = values[values.length - 1];
  const change = end - start;
  const changePct = start ? (change / start) * 100 : 0;
  let peak = values[0];
  let maxDD = 0;
  for (const v of values) {
    peak = Math.max(peak, v);
    maxDD = Math.max(maxDD, (peak - v) / peak);
  }
  return { start, end, change, changePct, maxDD };
});

const dexEntries = computed(() => {
  if (!summary.value?.dex_equity) return [];
  return Object.entries(summary.value.dex_equity)
    .filter(([, v]) => (v as number) > 0)
    .map(([dex, v]) => ({ name: dex === '' ? '主账户' : dex, value: v as number }));
});

const reasonBreakdown = computed(() => {
  const map = new Map<string, { n: number; pnl: number }>();
  for (const t of closedTrades.value) {
    const key = reasonLabel(t.reason);
    const cur = map.get(key) || { n: 0, pnl: 0 };
    cur.n += 1;
    cur.pnl += t.pnl_pct || 0;
    map.set(key, cur);
  }
  return [...map.entries()].sort((a, b) => b[1].pnl - a[1].pnl).map(([reason, v]) => ({ reason, ...v }));
});

function reasonLabel(r: string) {
  if (!r) return '未知';
  const map: Record<string, string> = {
    max_loss: '最大亏损止损', take_profit: '止盈', external_close_backfill: '外部平仓回填',
    hard_timeout: '超时强平', stale_flat: '盘口静止', signal_reverse: '信号反转', manual: '手动平仓',
  };
  for (const [k, v] of Object.entries(map)) if (r.startsWith(k)) return v;
  return r.length > 16 ? r.slice(0, 16) + '…' : r;
}

function buildEquityOption() {
  const data = curve.value.map((p) => [p.ts, p.equity]);
  if (!data.length) { equityOption.value = {}; return; }
  const rising = data[data.length - 1][1] >= data[0][1];
  const color = rising ? '#10b981' : '#f43f5e';
  equityOption.value = {
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const p = params[0];
        if (!p) return '';
        return `${new Date(p.data[0]).toLocaleString('zh-CN', { hour12: false })}<br/>权益: <strong>$${Number(p.data[1]).toFixed(2)}</strong>`;
      },
    },
    grid: { left: 56, right: 16, top: 20, bottom: 28 },
    xAxis: { type: 'time', axisLine: { lineStyle: { color: '#475569' } }, axisLabel: { color: '#94a3b8' }, splitLine: { show: false } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: 'rgba(148,163,184,0.1)' } }, axisLabel: { color: '#94a3b8', formatter: (v: number) => '$' + v.toFixed(0) } },
    series: [{
      type: 'line', data, smooth: true, showSymbol: false,
      lineStyle: { color, width: 2 },
      areaStyle: {
        color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [
          { offset: 0, color: rising ? 'rgba(16,185,129,0.2)' : 'rgba(244,63,94,0.2)' },
          { offset: 1, color: 'rgba(0,0,0,0)' },
        ] },
      },
    }],
  };
}

function buildDexOption() {
  if (!dexEntries.value.length) { dexOption.value = {}; return; }
  dexOption.value = {
    tooltip: { trigger: 'item', formatter: '{b}: ${c} ({d}%)' },
    series: [{
      type: 'pie', radius: ['52%', '78%'], center: ['50%', '50%'],
      itemStyle: { borderColor: 'transparent', borderWidth: 2 },
      label: { color: '#94a3b8', fontSize: 11, formatter: '{b}\n${c}' },
      data: dexEntries.value,
      color: ['#10b981', '#38bdf8', '#a78bfa', '#fbbf24', '#f472b6', '#34d399'],
    }],
  };
}

function buildReasonOption() {
  const items = reasonBreakdown.value;
  if (!items.length) { reasonOption.value = {}; return; }
  reasonOption.value = {
    tooltip: { trigger: 'axis', formatter: (p: any) => `${p[0].name}<br/>笔数: ${p[0].value}<br/>PnL合计: ${p[0].data.pnl.toFixed(2)}%` },
    grid: { left: 10, right: 50, top: 10, bottom: 10, containLabel: true },
    xAxis: { type: 'value', splitLine: { lineStyle: { color: 'rgba(148,163,184,0.1)' } }, axisLabel: { color: '#94a3b8' } },
    yAxis: { type: 'category', data: items.map((i) => i.reason), axisLabel: { color: '#cbd5e1', fontSize: 11 } },
    series: [{
      type: 'bar', barMaxWidth: 18,
      data: items.map((i) => ({ value: i.n, pnl: i.pnl, itemStyle: { color: i.pnl >= 0 ? '#10b981' : '#f43f5e' } })),
      label: { show: true, position: 'right', color: '#94a3b8', fontSize: 10, formatter: (p: any) => p.value + '笔' },
    }],
  };
}

async function loadEquity() {
  equityLoading.value = true;
  try {
    const [curveRes, sumRes, tradesRes] = await Promise.all([
      http.get('/api/portal/trader/api/dashboard/equity-curve', { params: { range_s: ranges[activeRange.value].seconds } }),
      http.get('/api/portal/trader/api/dashboard/summary'),
      http.get('/api/portal/trader/api/dashboard/closed-trades', { params: { limit: 200 } }),
    ]);
    curve.value = Array.isArray(curveRes.data) ? curveRes.data : [];
    summary.value = sumRes.data;
    closedTrades.value = Array.isArray(tradesRes.data) ? tradesRes.data : [];
    await nextTick();
    buildEquityOption();
    buildDexOption();
    buildReasonOption();
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '加载权益数据失败');
  } finally {
    equityLoading.value = false;
  }
}

watch(activeRange, loadEquity);
watch(tab, (v) => { if (v === 'equity' && !curve.value.length) loadEquity(); });

let equityTimer: number | null = null;
onMounted(() => {
  refreshMarket();
  equityTimer = window.setInterval(() => {
    if (tab.value === 'equity') loadEquity();
    else refreshMarket();
  }, 30000);
});
onUnmounted(() => { if (equityTimer) window.clearInterval(equityTimer); });
</script>

<template>
  <div class="space-y-5">
    <header class="flex items-center justify-between flex-wrap gap-3">
      <div>
        <h2 class="text-xl font-semibold">深度分析</h2>
        <p class="text-sm text-[var(--text-muted)] mt-1">行情技术分析与账户权益表现</p>
      </div>
      <div class="flex gap-1 p-1 rounded-lg bg-[var(--surface)] border border-[var(--border)]">
        <button class="px-4 py-1.5 rounded-md text-sm transition-colors" :class="tab === 'market' ? 'bg-violet-500/20 text-violet-300' : 'text-[var(--text-muted)] hover:text-white'" @click="tab = 'market'">📈 行情分析</button>
        <button class="px-4 py-1.5 rounded-md text-sm transition-colors" :class="tab === 'equity' ? 'bg-violet-500/20 text-violet-300' : 'text-[var(--text-muted)] hover:text-white'" @click="tab = 'equity'">💰 权益分析</button>
      </div>
    </header>

    <!-- 行情视图 -->
    <template v-if="tab === 'market'">
      <div class="flex gap-2 flex-wrap">
        <select class="input" v-model="coin">
          <option v-for="c in coins" :key="c" :value="c">{{ c }}</option>
        </select>
        <select class="input" v-model="interval">
          <option value="1m">1 分钟</option>
          <option value="5m">5 分钟</option>
          <option value="15m">15 分钟</option>
          <option value="1h">1 小时</option>
          <option value="4h">4 小时</option>
          <option value="1d">日线</option>
        </select>
        <button class="btn" @click="refreshMarket">🔄</button>
      </div>

      <div v-if="price" class="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">最新价</div>
          <div class="text-2xl font-semibold mt-1">${{ price?.price ?? price?.[coin] ?? '—' }}</div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">24h 最高</div>
          <div class="text-2xl font-semibold mt-1 text-emerald-400">${{ price?.high ?? '—' }}</div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">24h 最低</div>
          <div class="text-2xl font-semibold mt-1 text-rose-400">${{ price?.low ?? '—' }}</div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">24h 成交量</div>
          <div class="text-2xl font-semibold mt-1">{{ price?.volume ?? '—' }}</div>
        </div>
      </div>

      <div v-if="loading" class="card text-center py-10 text-[var(--text-muted)]">加载中...</div>
      <div v-else-if="error" class="card text-rose-300">⚠️ {{ error }}</div>
      <div v-else class="card"><v-chart :option="candleOption" class="chart-candle" autoresize /></div>

      <div v-if="orderbook" class="grid md:grid-cols-2 gap-5">
        <div class="card">
          <h3 class="font-semibold mb-3 text-rose-400">卖盘 Asks</h3>
          <div class="space-y-1 text-xs font-mono">
            <div v-for="(level, i) in (orderbook.asks || []).slice(0, 10)" :key="'a'+i" class="flex justify-between">
              <span class="text-rose-400">{{ level[0] }}</span><span>{{ level[1] }}</span>
            </div>
          </div>
        </div>
        <div class="card">
          <h3 class="font-semibold mb-3 text-emerald-400">买盘 Bids</h3>
          <div class="space-y-1 text-xs font-mono">
            <div v-for="(level, i) in (orderbook.bids || []).slice(0, 10)" :key="'b'+i" class="flex justify-between">
              <span class="text-emerald-400">{{ level[0] }}</span><span>{{ level[1] }}</span>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- 权益视图 -->
    <template v-else>
      <div class="flex items-center gap-2">
        <button v-for="(r, key) in ranges" :key="key"
          class="px-3 py-1 text-xs rounded-full border transition-colors"
          :class="activeRange === key ? 'bg-cyan-500/15 text-cyan-300 border-cyan-500/40' : 'text-[var(--text-muted)] border-[var(--border)] hover:bg-[var(--surface-hover)]'"
          @click="activeRange = key as RangeKey">{{ r.label }}</button>
        <button class="btn text-xs ml-auto" @click="loadEquity">🔄 刷新</button>
      </div>

      <div class="grid grid-cols-2 md:grid-cols-5 gap-4">
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">起始权益</div>
          <div class="text-lg font-semibold mt-1">{{ rangeStats ? '$' + rangeStats.start.toFixed(2) : '—' }}</div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">当前权益</div>
          <div class="text-lg font-semibold mt-1 text-emerald-400">{{ rangeStats ? '$' + rangeStats.end.toFixed(2) : '—' }}</div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">变动</div>
          <div class="text-lg font-semibold mt-1" :class="(rangeStats?.change ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'">
            {{ rangeStats ? ((rangeStats.change >= 0 ? '+' : '') + '$' + rangeStats.change.toFixed(2)) : '—' }}
          </div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">变动%</div>
          <div class="text-lg font-semibold mt-1" :class="(rangeStats?.changePct ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'">
            {{ rangeStats ? (rangeStats.changePct >= 0 ? '+' : '') + rangeStats.changePct.toFixed(2) + '%' : '—' }}
          </div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">最大回撤</div>
          <div class="text-lg font-semibold mt-1 text-rose-400">{{ rangeStats ? (rangeStats.maxDD * 100).toFixed(2) + '%' : '—' }}</div>
        </div>
      </div>

      <div v-if="equityLoading && !curve.length" class="card text-center py-10 text-[var(--text-muted)]">加载中...</div>
      <template v-else>
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-5">
          <div class="card lg:col-span-2">
            <h3 class="font-semibold mb-2">权益曲线</h3>
            <v-chart :option="equityOption" class="chart-md" autoresize />
          </div>
          <div class="card">
            <h3 class="font-semibold mb-2">DEX 分布</h3>
            <div v-if="!dexEntries.length" class="text-center text-[var(--text-muted)] py-16 text-sm">暂无 DEX 数据</div>
            <v-chart v-else :option="dexOption" class="chart-md" autoresize />
          </div>
        </div>
        <div class="card">
          <h3 class="font-semibold mb-2">按平仓原因统计 PnL</h3>
          <div v-if="!reasonBreakdown.length" class="text-center text-[var(--text-muted)] py-8 text-sm">暂无平仓数据</div>
          <v-chart v-else :option="reasonOption" class="chart-sm" autoresize />
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.chart-candle { height: 320px; }
.chart-md { height: 260px; }
.chart-sm { height: 220px; }
@media (min-width: 768px) {
  .chart-candle { height: 400px; }
  .chart-md { height: 320px; }
  .chart-sm { height: 260px; }
}
@media (min-width: 1024px) {
  .chart-candle { height: 480px; }
  .chart-md { height: 360px; }
  .chart-sm { height: 280px; }
}
</style>
