<script setup lang="ts">
/**
 * 系统监控（原生页，数据源 Prometheus，经 BFF /api/portal/monitoring 代理）。
 *
 * 只传预注册 query key，BFF 固定 PromQL；本页不直接接触 Prometheus。
 * 30s 自动刷新；离开页面清理定时器。
 */
import { onMounted, onUnmounted, ref, shallowRef } from 'vue';
import http from '@/shared/api/client';
import VChart from 'vue-echarts';
import { use } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { LineChart } from 'echarts/charts';
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components';

use([CanvasRenderer, LineChart, GridComponent, LegendComponent, TooltipComponent]);

const RANGE_S = 6 * 3600; // 展示最近 6 小时
const STEP = '60s';
const REFRESH_MS = 30000;

const loading = ref(false);
const error = ref('');

// 顶部统计（瞬时值）
const stats = ref<{ label: string; value: string; tone: string }[]>([]);

// 各图 option
const equityOpt = shallowRef<any>({});
const pnlPositionsOpt = shallowRef<any>({});
const heartbeatOpt = shallowRef<any>({});
const riskBlocksOpt = shallowRef<any>({});
const llmOpt = shallowRef<any>({});
const systemOpt = shallowRef<any>({});

interface RangeResult {
  resultType: string;
  result: any[];
}

async function fetchRange(key: string, range = '1h'): Promise<RangeResult> {
  const end = Math.floor(Date.now() / 1000);
  const start = end - RANGE_S;
  const { data } = await http.get('/api/portal/monitoring/query_range', {
    params: { key, start, end, step: STEP, range },
  });
  return data as RangeResult;
}

async function fetchInstant(key: string, range = '1h'): Promise<number | null> {
  const { data } = await http.get('/api/portal/monitoring/query', {
    params: { key, range },
  });
  const r = (data as RangeResult).result;
  if (!r || !r.length) return null;
  return Number(r[r.length - 1].value?.[1]);
}

// 把 matrix 结果转成 ECharts series；series 名用 metric 标签拼接
function toLineSeries(data: RangeResult, nameOf: (m: any) => string) {
  const xSet = new Set<number>();
  const series: { name: string; type: 'line'; data: [number, number][]; showSymbol: boolean }[] = [];
  for (const m of data.result) {
    const pts: [number, number][] = (m.values || []).map((v: [number, string]) => {
      xSet.add(v[0]);
      return [v[0] * 1000, Number(v[1])];
    });
    series.push({ name: nameOf(m.metric || {}), type: 'line', data: pts, showSymbol: false });
  }
  return { series };
}

function baseOption(unit: string, data: RangeResult, nameOf: (m: any) => string) {
  const { series } = toLineSeries(data, nameOf);
  return {
    tooltip: { trigger: 'axis' },
    legend: { textStyle: { color: '#94a3b8' }, top: 0 },
    grid: { left: 56, right: 20, top: 34, bottom: 28 },
    xAxis: { type: 'time', axisLine: { lineStyle: { color: '#475569' } } },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: { formatter: (v: number) => fmtAxis(v, unit) },
      splitLine: { lineStyle: { color: 'rgba(148,163,184,0.12)' } },
    },
    series,
  };
}

function fmtAxis(v: number, unit: string): string {
  if (unit === 'usd') return '$' + v.toFixed(v < 100 ? 2 : 0);
  if (unit === 'pct') return (v * 100).toFixed(1) + '%';
  if (unit === 'bytes') return v >= 1e9 ? (v / 1e9).toFixed(1) + 'G' : (v / 1e6).toFixed(0) + 'M';
  if (unit === 's') return v.toFixed(0) + 's';
  return v.toFixed(0);
}

function joinLabels(m: any, keys: string[], fallback: string): string {
  const parts = keys.filter((k) => m[k]).map((k) => m[k]);
  return parts.length ? parts.join(' ') : fallback;
}

async function load() {
  loading.value = true;
  error.value = '';
  try {
    // 顶部瞬时统计
    const [equity, positions, pnl, dd, alerts] = await Promise.all([
      fetchInstant('equity'),
      fetchInstant('open_positions'),
      fetchInstant('unrealized_pnl'),
      fetchInstant('drawdown'),
      fetchInstant('alerts_firing'),
    ]);
    stats.value = [
      { label: '权益', value: equity == null ? '-' : '$' + equity.toFixed(2), tone: 'neutral' },
      { label: '持仓数', value: positions == null ? '-' : String(positions), tone: 'neutral' },
      {
        label: '未实现盈亏',
        value: pnl == null ? '-' : '$' + pnl.toFixed(2),
        tone: (pnl ?? 0) >= 0 ? 'good' : 'bad',
      },
      {
        label: '回撤',
        value: dd == null ? '-' : (dd * 100).toFixed(1) + '%',
        tone: (dd ?? 0) > 0.15 ? 'bad' : dd ?? 0 > 0.08 ? 'warn' : 'good',
      },
      {
        label: '在触发告警',
        value: alerts == null ? '-' : String(alerts),
        tone: (alerts ?? 0) > 0 ? 'bad' : 'good',
      },
    ];

    // 时序图
    const [eqD, posD, hbD, blkD, llmD, sysD] = await Promise.all([
      fetchRange('equity'),
      fetchRange('open_positions'),
      fetchRange('loop_hb_age'),
      fetchRange('risk_gate_blocks', '1h'),
      fetchRange('llm_circuit'),
      fetchRange('rss'),
    ]);
    equityOpt.value = baseOption('usd', eqD, () => '权益 USD');
    pnlPositionsOpt.value = baseOption('short', posD, () => '持仓数');
    heartbeatOpt.value = baseOption('s', hbD, (m) => joinLabels(m, ['arm'], 'loop'));
    riskBlocksOpt.value = baseOption('short', blkD, (m) => joinLabels(m, ['gate'], 'gate'));
    llmOpt.value = baseOption('short', llmD, () => 'LLM 熔断(1=开)');
    systemOpt.value = baseOption('bytes', sysD, (m) => joinLabels(m, ['role'], 'rss'));
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '监控数据加载失败（Prometheus 不可用？）';
  } finally {
    loading.value = false;
  }
}

let timer: number | undefined;
onMounted(() => {
  load();
  timer = window.setInterval(load, REFRESH_MS);
});
onUnmounted(() => {
  if (timer) window.clearInterval(timer);
});
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <h1 class="text-xl font-semibold text-slate-100">系统监控</h1>
      <div class="flex items-center gap-3">
        <span v-if="loading" class="text-xs text-slate-400">刷新中…</span>
        <button
          class="rounded-md bg-slate-700 px-3 py-1 text-xs text-slate-100 hover:bg-slate-600"
          @click="load"
        >
          立即刷新
        </button>
      </div>
    </div>

    <div v-if="error" class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
      {{ error }}
    </div>

    <!-- 顶部统计 -->
    <div class="grid grid-cols-2 gap-3 md:grid-cols-5">
      <div
        v-for="s in stats"
        :key="s.label"
        class="rounded-lg border border-slate-700/60 bg-slate-800/60 px-4 py-3"
      >
        <div class="text-xs text-slate-400">{{ s.label }}</div>
        <div
          class="mt-1 text-lg font-semibold"
          :class="{
            'text-slate-100': s.tone === 'neutral',
            'text-emerald-400': s.tone === 'good',
            'text-red-400': s.tone === 'bad',
            'text-amber-400': s.tone === 'warn',
          }"
        >
          {{ s.value }}
        </div>
      </div>
    </div>

    <!-- 图表 -->
    <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <div class="rounded-lg border border-slate-700/60 bg-slate-800/40 p-3">
        <VChart :option="equityOpt" autoresize style="height: 260px" />
      </div>
      <div class="rounded-lg border border-slate-700/60 bg-slate-800/40 p-3">
        <VChart :option="pnlPositionsOpt" autoresize style="height: 260px" />
      </div>
      <div class="rounded-lg border border-slate-700/60 bg-slate-800/40 p-3">
        <VChart :option="heartbeatOpt" autoresize style="height: 260px" />
      </div>
      <div class="rounded-lg border border-slate-700/60 bg-slate-800/40 p-3">
        <VChart :option="riskBlocksOpt" autoresize style="height: 260px" />
      </div>
      <div class="rounded-lg border border-slate-700/60 bg-slate-800/40 p-3">
        <VChart :option="llmOpt" autoresize style="height: 260px" />
      </div>
      <div class="rounded-lg border border-slate-700/60 bg-slate-800/40 p-3">
        <VChart :option="systemOpt" autoresize style="height: 260px" />
      </div>
    </div>
  </div>
</template>
