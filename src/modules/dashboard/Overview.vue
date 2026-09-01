<script setup lang="ts">
/**
 * 总览仪表盘
 * - 通过 BFF 代理调用 hermes-trader 的 /api/dashboard/* 接口
 * - 展示账户摘要、DEX 分布、权益曲线（ECharts 时间轴）、最近平仓
 * - 加载状态 / 错误处理 / 30s 自动刷新
 */
import { computed, onMounted, onUnmounted, ref, shallowRef } from 'vue';
import http from '@/shared/api/client';
import VChart from 'vue-echarts';
import { use } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import { useToast } from '@/stores/toast';
import { useSseFeedStore } from '@/stores/sseFeed';
import { useRiskCardsStore } from '@/stores/riskCards';
import RiskStatusCard from './components/RiskStatusCard.vue';
import LiquidationAlertCard from './components/LiquidationAlertCard.vue';
import FeedMonitorCard from './components/FeedMonitorCard.vue';

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent]);

const toast = useToast();

const loading = ref(true);
const error = ref('');
const summary = ref<any>(null);
const closedTrades = ref<any[]>([]);
const equityOption = shallowRef<any>({});
let timer: number | null = null;
let sseUnsub: (() => void) | null = null;

// SSE 事件驱动更新：loop_heartbeat 携带 equity/available/daily_pnl/open_positions/dex_*
// 到达时直接更新 summary KPI，无需等 60s 轮询；position_update 更新持仓数。
// 权益曲线仍靠 60s fetchAll 兜底刷新（曲线数据不在 SSE 事件中）。
function handleSseEvent(data: any) {
  if (!data?.event || !summary.value) return;
  if (data.event === 'loop_heartbeat') {
    if (data.equity !== undefined) summary.value.equity = data.equity;
    if (data.available !== undefined) summary.value.available = data.available;
    if (data.daily_pnl !== undefined) summary.value.daily_pnl = data.daily_pnl;
    if (data.open_positions !== undefined) summary.value.open_positions = data.open_positions;
    if (data.dex_equity) summary.value.dex_equity = data.dex_equity;
    if (data.dex_available) summary.value.dex_available = data.dex_available;
  } else if (data.event === 'position_update') {
    if (data.count !== undefined) summary.value.open_positions = data.count;
  }
}

async function fetchAll() {
  try {
    error.value = '';
    const [s, eq, t] = await Promise.all([
      http.get('/api/portal/trader/api/dashboard/summary'),
      http.get('/api/portal/trader/api/dashboard/equity-curve'),
      http.get('/api/portal/trader/api/dashboard/closed-trades'),
    ]);
    summary.value = s.data;
    closedTrades.value = Array.isArray(t.data) ? t.data : (t.data?.trades || []);

    // 权益曲线（时间轴）
    const curve: any[] = eq.data?.curve || eq.data?.equity_curve || (Array.isArray(eq.data) ? eq.data : []);
    equityOption.value = {
      backgroundColor: 'transparent',
      grid: { left: 60, right: 20, top: 20, bottom: 30 },
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#0B0E14',
        borderColor: '#232A3B',
        textStyle: { color: '#E5E7EB', fontSize: 11 },
        formatter: (params: any) => {
          const p = params[0];
          if (!p) return '';
          const ts = p.data[0];
          const time = new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
          return `${time}<br/>权益: <strong>$${Number(p.data[1]).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong>`;
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
        axisLabel: {
          color: '#6B7280',
          fontSize: 10,
          formatter: (v: number) => `$${v.toFixed(0)}`,
        },
      },
      series: [
        {
          type: 'line',
          data: curve.map((d: any) => {
            const ts = typeof d.ts === 'number' ? (d.ts < 1e12 ? d.ts * 1000 : d.ts) : Date.now();
            const eq = typeof d === 'number' ? d : (d.equity ?? d.value ?? 0);
            return [ts, Number(eq.toFixed(2))] as [number, number];
          }),
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#22C55E', width: 2 },
          areaStyle: {
            color: {
              type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(34,197,94,0.18)' },
                { offset: 1, color: 'rgba(34,197,94,0)' },
              ],
            },
          },
        },
      ],
    };
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || '加载仪表盘数据失败';
  } finally {
    loading.value = false;
  }
}

function fmt(v: any, digits = 2) {
  if (v === null || v === undefined || isNaN(Number(v))) return '—';
  return Number(v).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function fmtDateTime(ts: number) {
  if (!ts) return '—';
  const ms = ts < 1e12 ? ts * 1000 : ts;
  return new Date(ms).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function pnlColor(v: number) {
  if (v > 0) return 'text-emerald-400';
  if (v < 0) return 'text-rose-400';
  return 'text-[var(--text-muted)]';
}

function sideLabel(s: string) {
  if (!s) return '—';
  return s.toLowerCase() === 'long' ? '做多' : '做空';
}

const SOURCE_LABELS: Record<string, string> = {
  dsl: 'DSL', manual: '手动', external: '外部', reconcile: '对账', ai: 'AI',
};
function sourceLabel(s: string) {
  return (s && SOURCE_LABELS[s]) || s || '—';
}

// DEX 分布（仅显示权益 > 0 的账户，主账户显示为"主账户"）
const dexEntries = computed(() => {
  if (!summary.value?.dex_equity) return [];
  return Object.entries(summary.value.dex_equity)
    .filter(([, equity]) => Number(equity) > 0)
    .map(([dex, equity]) => ({
      dex: dex === '' ? '主账户' : dex,
      equity: Number(equity),
      available: Number(summary.value!.dex_available?.[dex] ?? 0),
    }))
    .sort((a, b) => b.equity - a.equity);
});

const dailyPnlPositive = computed(() => (summary.value?.daily_pnl ?? 0) >= 0);

onMounted(() => {
  fetchAll();
  // SSE 事件驱动：loop_heartbeat 实时更新 KPI 卡片，position_update 更新持仓数
  const sse = useSseFeedStore();
  sseUnsub = sse.onEvent(handleSseEvent);
  // 风控三卡：独立 5s 轮询 risk-status + positions（store 内启动/停止）
  useRiskCardsStore().start();
  // 60s 兜底轮询：刷新权益曲线 + 最近平仓表（SSE 不携带曲线数据）
  timer = window.setInterval(fetchAll, 60000);
});
onUnmounted(() => {
  if (timer) window.clearInterval(timer);
  sseUnsub?.();
  sseUnsub = null;
  useRiskCardsStore().stop();
});
</script>

<template>
  <div class="space-y-5">
    <header class="flex items-center justify-between">
      <div>
        <h2 class="text-xl font-semibold">交易总览</h2>
        <p class="text-sm text-[var(--text-muted)] mt-1">账户摘要、DEX 分布与权益走势（SSE 实时 + 60s 兜底）</p>
      </div>
      <button class="btn" @click="fetchAll">🔄 刷新</button>
    </header>

    <div v-if="loading" class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div v-for="i in 4" :key="i" class="card h-24 animate-pulse bg-[var(--surface-hover)]"></div>
    </div>

    <div v-else-if="error" class="card border-rose-500/40 bg-rose-500/10 text-rose-300">
      ⚠️ {{ error }}
      <button class="btn ml-3" @click="fetchAll">重试</button>
    </div>

    <template v-else>
      <!-- KPI 卡片 -->
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">账户权益</div>
          <div class="text-2xl font-semibold mt-1 text-emerald-400">${{ fmt(summary?.equity) }}</div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">可用余额</div>
          <div class="text-2xl font-semibold mt-1 text-cyan-400">${{ fmt(summary?.available) }}</div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">今日盈亏</div>
          <div class="text-2xl font-semibold mt-1" :class="pnlColor(summary?.daily_pnl ?? 0)">
            {{ dailyPnlPositive ? '+' : '' }}${{ fmt(summary?.daily_pnl) }}
            <span class="text-sm ml-1">({{ (summary?.daily_pnl_pct ?? 0).toFixed(2) }}%)</span>
          </div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">持仓数</div>
          <div class="text-2xl font-semibold mt-1 text-purple-400">{{ summary?.open_positions ?? 0 }}</div>
        </div>
      </div>

      <!-- 风控三卡：熔断/日亏闸 · 强平价预警 · 行情流监控（10x 杠杆核心风控可视化） -->
      <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
        <RiskStatusCard />
        <LiquidationAlertCard />
        <FeedMonitorCard />
      </div>

      <!-- 权益曲线 + DEX 分布 -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div class="card lg:col-span-2">
          <div class="flex items-center justify-between mb-3">
            <h3 class="font-semibold">权益曲线（24h）</h3>
          </div>
          <v-chart :option="equityOption" class="chart-equity" autoresize />
        </div>

        <div class="card">
          <h3 class="font-semibold mb-3">DEX 分布</h3>
          <div v-if="dexEntries.length === 0" class="text-sm text-[var(--text-muted)] py-10 text-center">
            暂无 DEX 数据
          </div>
          <div v-else class="space-y-3">
            <div
              v-for="e in dexEntries"
              :key="e.dex"
              class="flex items-center justify-between border-b border-[var(--border)] pb-2 last:border-0"
            >
              <div>
                <div class="text-sm font-medium">{{ e.dex }}</div>
                <div class="text-xs text-[var(--text-muted)]">可用: ${{ fmt(e.available) }}</div>
              </div>
              <div class="text-right">
                <div class="text-sm font-mono font-bold">${{ fmt(e.equity) }}</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 最近平仓 -->
      <div class="card">
        <div class="flex items-center justify-between mb-3">
          <h3 class="font-semibold">最近平仓</h3>
          <span v-if="closedTrades.length" class="text-xs text-[var(--text-muted)]">
            最近 {{ closedTrades.length }} 笔
          </span>
        </div>
        <div v-if="closedTrades.length === 0" class="text-sm text-[var(--text-muted)] py-8 text-center">
          暂无平仓记录
        </div>
        <div v-else class="overflow-x-auto">
          <table class="w-full text-xs">
            <thead>
              <tr class="text-[var(--text-muted)] border-b border-[var(--border)]">
                <th class="text-left py-2 px-3 font-medium">时间</th>
                <th class="text-left py-2 px-3 font-medium">币种</th>
                <th class="text-left py-2 px-3 font-medium">方向</th>
                <th class="text-right py-2 px-3 font-medium">仓位</th>
                <th class="text-right py-2 px-3 font-medium">盈亏</th>
                <th class="text-left py-2 px-3 font-medium">原因</th>
                <th class="text-left py-2 px-3 font-medium">来源</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(t, i) in closedTrades"
                :key="i"
                class="border-b border-[var(--border)] last:border-0 hover:bg-[var(--surface-hover)]"
              >
                <td class="py-2.5 px-3 font-mono text-[var(--text-muted)] whitespace-nowrap">
                  {{ fmtDateTime(t.ts) }}
                </td>
                <td class="py-2.5 px-3 font-mono font-medium">{{ t.coin || '?' }}</td>
                <td class="py-2.5 px-3">
                  <span :class="t.side === 'long' ? 'badge-ok' : 'badge-danger'">{{ sideLabel(t.side) }}</span>
                </td>
                <td class="py-2.5 px-3 font-mono text-right">{{ t.leverage || '—' }}x</td>
                <td class="py-2.5 px-3 font-mono text-right font-medium" :class="pnlColor(t.pnl_pct ?? 0)">
                  {{ (t.pnl_pct ?? 0) >= 0 ? '+' : '' }}{{ (t.pnl_pct ?? 0).toFixed(2) }}%
                </td>
                <td class="py-2.5 px-3 text-[var(--text-muted)] max-w-[200px] truncate" :title="t.reason">
                  {{ t.reason || '—' }}
                </td>
                <td class="py-2.5 px-3">
                  <span class="badge-purple">{{ sourceLabel(t.source) }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.chart-equity { height: 240px; }
@media (min-width: 768px) {
  .chart-equity { height: 300px; }
}
</style>
