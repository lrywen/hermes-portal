<script setup lang="ts">
/**
 * Funding-carry shadow forward 监控（原生页）。
 *
 * 数据源：trader 侧 /api/dashboard/research/funding-carry-forward，经 BFF
 * /api/portal/trader 代理（BFF 要求 dashboard:read）。展示极端 funding 信号
 * 的三口径（theoretical_neutral / perp_hedge / bare_directional）72h 结算结果。
 * 60s 自动刷新；离开页面清理定时器。
 */
import { computed, onMounted, onUnmounted, ref, shallowRef } from 'vue';
import http from '@/shared/api/client';
import VChart from 'vue-echarts';
import { use } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { BarChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';

use([CanvasRenderer, BarChart, GridComponent, TooltipComponent]);

const REFRESH_MS = 60000;

const loading = ref(false);
const error = ref('');
const present = ref(false);
const counts = ref({ total: 0, open: 0, settled: 0 });
const summary = ref<Record<string, { n: number; mean: number; win: number }>>({});
const signals = shallowRef<any[]>([]);

const LENS_LABEL: Record<string, string> = {
  theoretical_neutral: '理论中性',
  perp_hedge: 'Perp 对冲',
  bare_directional: '裸方向',
};
const LENS_ORDER = ['theoretical_neutral', 'perp_hedge', 'bare_directional'];

const statCards = computed(() => [
  { label: '信号总数', value: String(counts.value.total) },
  { label: '未结算', value: String(counts.value.open) },
  { label: '已结算', value: String(counts.value.settled) },
]);

const barOption = computed(() => {
  const names: string[] = [];
  const means: number[] = [];
  for (const k of LENS_ORDER) {
    const s = summary.value[k];
    if (s) {
      names.push(LENS_LABEL[k]);
      means.push(s.mean);
    }
  }
  return {
    tooltip: { trigger: 'axis', valueFormatter: (v: any) => `${Number(v).toFixed(1)} bp` },
    grid: { left: 56, right: 20, top: 24, bottom: 36 },
    xAxis: { type: 'category', data: names, axisLine: { lineStyle: { color: '#475569' } } },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: { formatter: '{value}bp' },
      splitLine: { lineStyle: { color: 'rgba(148,163,184,0.12)' } },
    },
    series: [
      {
        type: 'bar',
        data: means.map((v) => ({
          value: v,
          itemStyle: { color: v >= 0 ? '#34d399' : '#f87171' },
        })),
        barWidth: 42,
      },
    ],
  };
});

// 表格行：最近信号在前，格式化时间/各口径
const rows = computed(() =>
  [...signals.value]
    .sort((a, b) => (b.time || 0) - (a.time || 0) || (b.sig_hour || 0) - (a.sig_hour || 0))
    .map((s) => ({
      coin: s.coin,
      time: fmtTime(s.time),
      fr: fmtSignedPct(s.fundingRate),
      hedge: s.hedge_coin ? `${s.hedge_coin}${s.hedge_corr != null ? ` (${s.hedge_corr})` : ''}` : '—',
      status: s.status === 'settled' ? '已结算' : '未结算',
      neutral: fmtBp(s.theoretical_neutral),
      hedgePnl: fmtBp(s.perp_hedge),
      bare: fmtBp(s.bare_directional),
    })),
);

let timer: number | undefined;

async function load() {
  loading.value = true;
  error.value = '';
  try {
    const { data } = await http.get(
      '/api/portal/trader/api/dashboard/research/funding-carry-forward',
    );
    present.value = !!data.present;
    counts.value = data.counts || { total: 0, open: 0, settled: 0 };
    summary.value = data.summary || {};
    signals.value = data.signals || [];
    if (!present.value) error.value = data.note || '暂无 forward 数据';
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '加载失败';
  } finally {
    loading.value = false;
  }
}

function fmtTime(ms: number): string {
  if (!ms) return '—';
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function fmtSignedPct(v: number): string {
  if (v == null || Number.isNaN(v)) return '—';
  return `${(v * 100).toFixed(4)}%`;
}
function fmtBp(v: number): string {
  if (v == null || Number.isNaN(v)) return '—';
  return `${v.toFixed(1)}`;
}
function cls(bpText: string): string {
  if (bpText === '—') return '';
  const v = Number(bpText);
  return v > 0 ? 'pos' : v < 0 ? 'neg' : '';
}

onMounted(() => {
  load();
  timer = window.setInterval(load, REFRESH_MS);
});
onUnmounted(() => {
  if (timer) window.clearInterval(timer);
});
</script>

<template>
  <div class="page">
    <div class="head">
      <h2>Funding-Carry 前瞻监控</h2>
      <span class="sub">极端 funding 信号 · 72h 三口径结算（bp）</span>
      <button class="refresh" :disabled="loading" @click="load">
        {{ loading ? '刷新中…' : '刷新' }}
      </button>
    </div>

    <p v-if="error" class="error">{{ error }}</p>

    <div class="cards">
      <div v-for="c in statCards" :key="c.label" class="card">
        <div class="card-label">{{ c.label }}</div>
        <div class="card-value">{{ c.value }}</div>
      </div>
      <div v-for="k in LENS_ORDER" :key="k" class="card" v-show="summary[k]">
        <div class="card-label">{{ LENS_LABEL[k] }} 均值 / 胜率</div>
        <div class="card-value" :class="summary[k]?.mean >= 0 ? 'pos' : 'neg'">
          {{ summary[k]?.mean.toFixed(1) }}bp
          <span class="win">{{ (summary[k]?.win * 100).toFixed(0) }}%</span>
        </div>
      </div>
    </div>

    <div class="panel">
      <VChart v-if="Object.keys(summary).length" :option="barOption" autoresize style="height: 260px" />
      <p v-else class="empty">尚无已结算信号</p>
    </div>

    <div class="panel">
      <table class="tbl">
        <thead>
          <tr>
            <th>币种</th>
            <th>信号时间</th>
            <th>Funding</th>
            <th>对冲腿 (相关)</th>
            <th>状态</th>
            <th>理论中性</th>
            <th>Perp对冲</th>
            <th>裸方向</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(r, i) in rows" :key="i">
            <td class="coin">{{ r.coin }}</td>
            <td>{{ r.time }}</td>
            <td>{{ r.fr }}</td>
            <td>{{ r.hedge }}</td>
            <td>{{ r.status }}</td>
            <td :class="cls(r.neutral)">{{ r.neutral }}</td>
            <td :class="cls(r.hedgePnl)">{{ r.hedgePnl }}</td>
            <td :class="cls(r.bare)">{{ r.bare }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="!rows.length" class="empty">暂无信号</p>
    </div>
  </div>
</template>

<style scoped>
.page { padding: 4px 2px 40px; }
.head { display: flex; align-items: baseline; gap: 12px; }
.head h2 { margin: 0; font-size: 18px; }
.sub { color: #94a3b8; font-size: 12px; }
.refresh { margin-left: auto; background: #1e293b; color: #e2e8f0; border: 1px solid #334155;
  border-radius: 6px; padding: 5px 14px; cursor: pointer; }
.refresh:disabled { opacity: 0.6; cursor: default; }
.error { color: #f87171; font-size: 13px; margin: 10px 0; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 12px; margin: 16px 0; }
.card { background: #0f172a; border: 1px solid #1e293b; border-radius: 10px; padding: 12px 14px; }
.card-label { color: #94a3b8; font-size: 12px; }
.card-value { font-size: 22px; font-weight: 600; margin-top: 4px; }
.win { font-size: 13px; color: #94a3b8; font-weight: 400; margin-left: 6px; }
.pos { color: #34d399; }
.neg { color: #f87171; }
.panel { background: #0f172a; border: 1px solid #1e293b; border-radius: 10px;
  padding: 12px; margin-top: 14px; }
.empty { color: #64748b; text-align: center; padding: 30px; font-size: 13px; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th { text-align: left; color: #94a3b8; font-weight: 500; padding: 8px 10px;
  border-bottom: 1px solid #1e293b; white-space: nowrap; }
.tbl td { padding: 8px 10px; border-bottom: 1px solid #172033; white-space: nowrap; }
.tbl .coin { font-weight: 600; color: #e2e8f0; }
</style>
