<script setup lang="ts">
/**
 * 强平价预警卡片（O-4 回移植自 hermes-web）。
 * 数据源：positions 每行的 liq_px（HL liquidationPx；完全保证金/极小仓位
 * 为 null → 不参与）。距强平距离 = 标记价与强平价的价差占比（见 shared/utils/liquidation）。
 * 距离越近越危险：<10% 红色告警，<20% 琥珀，其余绿色；无敞口显示紫色。
 */
import { computed } from 'vue';
import { useRiskCardsStore } from '@/stores/riskCards';
import { liqDistPct, liqLevel } from '@/shared/utils/liquidation';

const risk = useRiskCardsStore();

interface LiqRow {
  coin: string;
  side: string;
  mark: number;
  liq: number;
  distPct: number; // 距强平的价格百分比，始终为正
}

const rows = computed<LiqRow[]>(() => {
  const out: LiqRow[] = [];
  for (const p of risk.positions) {
    const dist = liqDistPct(p);
    if (dist == null) continue;
    out.push({ coin: p.coin, side: p.side, mark: p.mark_px, liq: p.liq_px, distPct: dist });
  }
  // 最危险（距强平最近）的排最前
  return out.sort((a, b) => a.distPct - b.distPct);
});

const closestLevel = computed(() => (rows.value.length ? liqLevel(rows.value[0].distPct) : null));

const HEAD_BADGE: Record<string, { cls: string; label: string }> = {
  danger: { cls: 'badge-danger', label: '高危' },
  warn: { cls: 'badge-warn', label: '注意' },
  safe: { cls: 'badge-ok', label: '安全' },
  none: { cls: 'badge-purple', label: '无敞口' },
};
const headState = computed(() => (closestLevel.value ? closestLevel.value : 'none'));

function distColor(distPct: number): string {
  const lv = liqLevel(distPct);
  if (lv === 'danger') return 'text-rose-400';
  if (lv === 'warn') return 'text-amber-400';
  return 'text-emerald-400';
}

function fmtPx(n: number): string {
  return Number(n).toLocaleString('en-US', { maximumFractionDigits: 2 });
}
</script>

<template>
  <div class="card p-4 flex flex-col">
    <div class="flex items-center justify-between mb-3">
      <h3 class="font-semibold text-sm">强平价预警</h3>
      <span class="text-xs px-2 py-0.5 rounded-full font-semibold" :class="HEAD_BADGE[headState].cls">
        {{ HEAD_BADGE[headState].label }}
      </span>
    </div>

    <div class="flex-1 space-y-2 overflow-auto">
      <div v-if="!rows.length" class="text-xs text-[var(--muted)] text-center py-8">
        暂无带强平价的持仓
      </div>
      <div
        v-for="r in rows"
        :key="r.coin"
        class="flex items-center justify-between gap-2 border-b border-[var(--border)] pb-2 last:border-0 last:pb-0"
      >
        <div class="min-w-0">
          <div class="flex items-center gap-1.5">
            <span class="text-sm font-mono font-medium">{{ r.coin }}</span>
            <span
              class="text-xs px-2 py-0.5 rounded-full font-semibold"
              :class="r.side === 'long' ? 'badge-ok' : 'badge-danger'"
            >
              {{ r.side === 'long' ? '多' : '空' }}
            </span>
          </div>
          <div class="text-[11px] text-[var(--muted)] font-mono mt-0.5">
            标记 {{ fmtPx(r.mark) }} · 强平 {{ fmtPx(r.liq) }}
          </div>
        </div>
        <div class="text-right shrink-0">
          <div class="text-sm font-mono font-bold" :class="distColor(r.distPct)">
            {{ r.distPct.toFixed(1) }}%
          </div>
          <div class="text-[11px] text-[var(--muted)]">距强平</div>
        </div>
      </div>
      <div v-if="rows.length" class="text-[11px] text-[var(--muted)] leading-4 pt-1 border-t border-[var(--border)]">
        距离 = 标记价与强平价的价差占比；&lt;10% 红色、&lt;20% 琥珀。完全保证金仓位无强平价，不参与排序。
      </div>
    </div>
  </div>
</template>
