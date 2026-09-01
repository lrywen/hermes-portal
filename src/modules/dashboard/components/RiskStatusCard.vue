<script setup lang="ts">
/**
 * 风控状态卡片（O-4 回移植自 her-web）。
 * 数据源：/api/dashboard/risk-status（经 riskCards store 5s 轮询）。
 * 三层风控态，严重度从高到低：
 *   1) 日亏硬闸 kill_armed —— 当日亏损触及限额，回路已全平停机（红）；
 *   2) 全局熔断 global_halt —— 暂停所有新开仓（红，显示剩余分钟）；
 *   3) 单币熔断 coin_circuits —— 个别币种暂停重入（琥珀，coin→剩余分钟）。
 * 另显示运行模式 mode 与当日盈亏/限额余量。
 */
import { computed } from 'vue';
import { useRiskCardsStore } from '@/stores/riskCards';

const risk = useRiskCardsStore();
const rs = computed<any>(() => risk.riskStatus);

type Level = 'danger' | 'warn' | 'safe' | 'unknown';

const overall = computed<Level>(() => {
  const r = rs.value;
  if (!r) return 'unknown';
  if (r.kill_armed || r.global_halt) return 'danger';
  if (r.armed_coins > 0) return 'warn';
  return 'safe';
});

const OVERALL_CLS: Record<Level, string> = {
  danger: 'badge-danger',
  warn: 'badge-warn',
  safe: 'badge-ok',
  unknown: 'badge-muted',
};
const OVERALL_LABEL: Record<Level, string> = {
  danger: '熔断中',
  warn: '部分熔断',
  safe: '正常',
  unknown: '加载中',
};

// 单币熔断按剩余分钟降序（最久的排前）
const coinEntries = computed(() => {
  const c = rs.value?.coin_circuits ?? {};
  return Object.entries(c)
    .map(([coin, min]) => ({ coin, min: Number(min) }))
    .sort((a, b) => b.min - a.min);
});

// 距日亏硬闸的余量：当日盈亏 - 限额（亏损日 pnl 为负、limit 为负）
const lossHeadroom = computed<number | null>(() => {
  const r = rs.value;
  if (!r || r.daily_loss_limit == null || r.daily_pnl == null) return null;
  return r.daily_pnl - r.daily_loss_limit;
});

function modeBadge(mode: string | null): string {
  if (!mode) return 'badge-warn';
  const m = mode.toUpperCase();
  if (m === 'LIVE') return 'badge-ok';
  if (m === 'OFF') return 'badge-danger';
  return 'badge-purple';
}

function fmtUsd(v: any): string {
  if (v === null || v === undefined || isNaN(Number(v))) return '—';
  const n = Number(v);
  return (n >= 0 ? '+' : '') + '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
</script>

<template>
  <div class="card p-4">
    <div class="flex items-center justify-between mb-3">
      <h3 class="font-semibold text-sm">风控状态</h3>
      <span class="text-xs px-2 py-0.5 rounded-full font-semibold" :class="OVERALL_CLS[overall]">
        {{ OVERALL_LABEL[overall] }}
      </span>
    </div>

    <div v-if="!rs" class="text-xs text-[var(--muted)] text-center py-8">
      风控状态加载中…
    </div>
    <div v-else class="space-y-2.5">
      <!-- 运行模式 -->
      <div class="flex items-center justify-between">
        <span class="text-sm text-[var(--muted)]">运行模式</span>
        <span class="text-xs px-2 py-0.5 rounded-full font-semibold" :class="modeBadge(rs.mode)">
          {{ rs.mode ?? '未知' }}
        </span>
      </div>

      <!-- 日亏硬闸 -->
      <div class="flex items-center justify-between">
        <span class="text-sm text-[var(--muted)]">日亏硬闸</span>
        <span v-if="rs.kill_armed" class="text-xs px-2 py-0.5 rounded-full font-semibold badge-danger">
          已触发 · 全平停机
        </span>
        <span v-else class="text-xs font-mono">
          今日 {{ fmtUsd(rs.daily_pnl) }}
          <template v-if="rs.daily_loss_limit != null">
            / 限额 {{ fmtUsd(rs.daily_loss_limit) }}
          </template>
        </span>
      </div>
      <div v-if="!rs.kill_armed && lossHeadroom != null" class="text-[11px] text-[var(--muted)] text-right -mt-1.5">
        距硬闸余量 {{ fmtUsd(lossHeadroom) }}
      </div>

      <!-- 全局熔断 -->
      <div class="flex items-center justify-between border-t border-[var(--border)] pt-2.5">
        <span class="text-sm text-[var(--muted)]">全局熔断</span>
        <span v-if="rs.global_halt" class="text-xs px-2 py-0.5 rounded-full font-semibold badge-danger">
          熔断中 · 剩余 {{ Number(rs.global_halt_remaining_min).toFixed(0) }}m
        </span>
        <span v-else class="text-xs px-2 py-0.5 rounded-full font-semibold badge-ok">未触发</span>
      </div>

      <!-- 单币熔断 -->
      <div class="border-t border-[var(--border)] pt-2.5">
        <div class="flex items-center justify-between mb-1.5">
          <span class="text-sm text-[var(--muted)]">单币熔断</span>
          <span class="text-xs font-mono text-[var(--muted)]">{{ rs.armed_coins }} 个币种</span>
        </div>
        <div v-if="!coinEntries.length" class="text-[11px] text-[var(--muted)]">
          无币种被熔断
        </div>
        <div v-else class="flex flex-wrap gap-1.5">
          <span
            v-for="e in coinEntries"
            :key="e.coin"
            class="text-xs px-2 py-0.5 rounded-full font-semibold badge-warn"
          >
            {{ e.coin }} · {{ e.min.toFixed(0) }}m
          </span>
        </div>
      </div>
    </div>
  </div>
</template>
