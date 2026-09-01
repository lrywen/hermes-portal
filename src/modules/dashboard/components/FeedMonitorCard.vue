<script setup lang="ts">
/**
 * 行情流监控卡片（O-4 回移植自 hermes-web）。
 * 两条独立的数据通道，任何一条断了都要能看出来：
 *   1) 交易回路心跳 —— trading_loop 每 tick 写 session log，后端 risk-status
 *      端点据此给出 feed_status（live/stale/offline）与 feed_age_s（5s 轮询）；
 *   2) SSE 推送流 —— 浏览器到 trader 的 EventSource 长连接（connected），
 *      以及最近一条 loop_heartbeat 事件距今的秒数（本地时钟计算）。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRiskCardsStore } from '@/stores/riskCards';
import { useSseFeedStore } from '@/stores/sseFeed';

const risk = useRiskCardsStore();
const sse = useSseFeedStore();

const STALE_AFTER_S = 180; // 与后端 stale_tick_age_s 默认阈值一致

// 本地 5s 滴答：无新事件时心跳年龄也会持续刷新
const now = ref(Date.now());
let tick: number | null = null;
onMounted(() => {
  tick = window.setInterval(() => {
    now.value = Date.now();
  }, 5000);
});
onUnmounted(() => {
  if (tick) window.clearInterval(tick);
});

type Level = 'live' | 'stale' | 'offline';

// 交易回路心跳：后端聚合的 feed_status（读 session log，跨进程可靠）；
// risk-status 尚未返回时判为离线。
const loopLevel = computed<Level>(() => {
  const st = risk.riskStatus?.feed_status;
  if (st === 'live' || st === 'stale' || st === 'offline') return st;
  return 'offline';
});
const loopAgeS = computed<number | null>(() => risk.riskStatus?.feed_age_s ?? null);

function fmtAge(ageS: number | null): string {
  if (ageS == null) return '无心跳';
  if (ageS < 60) return `${ageS}s 前`;
  if (ageS < 3600) return `${Math.floor(ageS / 60)}m ${ageS % 60}s 前`;
  return `${Math.floor(ageS / 3600)}h 前`;
}

// SSE 推送流：连接状态 + 最近一条心跳事件年龄。EventSource 自动重连，
// 未连接/无心跳/心跳过旧分别对应 中断/延迟/延迟。
const sseAgeS = computed<number | null>(() => {
  const hb = risk.lastHeartbeatTs;
  if (!hb) return null;
  return Math.max(0, Math.floor((now.value - hb) / 1000));
});

const sseLevel = computed<Level>(() => {
  if (!sse.connected) return 'offline';
  const age = sseAgeS.value;
  if (age == null || age > STALE_AFTER_S) return 'stale';
  return 'live';
});

const rows = computed(() => [
  {
    label: '交易回路心跳',
    level: loopLevel.value,
    detail: `${fmtAge(loopAgeS.value)} · ${risk.riskStatus?.open_positions ?? 0} 持仓`,
  },
  {
    label: 'SSE 实时推送',
    level: sseLevel.value,
    detail: sse.connected
      ? `已连接 · 心跳 ${fmtAge(sseAgeS.value)}`
      : '未连接（自动重连中）',
  },
]);

const META: Record<Level, { cls: string; text: string; label: string }> = {
  live: { cls: 'badge-ok', text: 'text-emerald-400', label: '正常' },
  stale: { cls: 'badge-warn', text: 'text-amber-400', label: '延迟' },
  offline: { cls: 'badge-danger', text: 'text-rose-400', label: '中断' },
};
</script>

<template>
  <div class="card p-4 flex flex-col">
    <h3 class="font-semibold text-sm mb-3">行情流监控</h3>
    <div class="flex-1 space-y-3">
      <div
        v-for="r in rows"
        :key="r.label"
        class="flex items-center justify-between gap-2"
      >
        <div class="flex items-center gap-2 min-w-0">
          <span
            class="inline-block w-2 h-2 rounded-full"
            :class="{
              'bg-emerald-400': r.level === 'live',
              'bg-amber-400': r.level === 'stale',
              'bg-rose-400': r.level === 'offline',
            }"
          />
          <span class="text-sm">{{ r.label }}</span>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <span class="text-xs text-[var(--muted)] font-mono truncate max-w-40">{{ r.detail }}</span>
          <span class="text-xs px-2 py-0.5 rounded-full font-semibold" :class="META[r.level].cls">
            {{ META[r.level].label }}
          </span>
        </div>
      </div>
      <div class="text-[11px] text-[var(--muted)] leading-4 pt-1 border-t border-[var(--border)]">
        心跳超过 {{ STALE_AFTER_S }}s 判为延迟；无心跳判为中断。回路心跳读交易日志，SSE 为浏览器实时推送通道。
      </div>
    </div>
  </div>
</template>
