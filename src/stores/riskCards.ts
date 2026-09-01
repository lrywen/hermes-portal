/**
 * 风控三卡数据源（O-4 回移植自 hermes-web）。
 *
 * 轮询两个 trader 端点（均经 BFF 代理，需登录 + dashboard:read）：
 *   - /api/dashboard/risk-status  5s：熔断/日亏闸/运行模式/行情馈送新鲜度
 *   - /api/dashboard/positions    5s：持仓 liq_px/mark_px（服务端自带 5s 缓存）
 *
 * SSE 侧订阅 loop_heartbeat 记录最近心跳时间戳，供 FeedMonitorCard
 * 判断"浏览器→trader 推送通道"的存活状态（与回路心跳是两条独立通道）。
 *
 * 由 Overview 页 onMounted 启动、onUnmounted 停止；start/stop 幂等。
 */
import { defineStore } from 'pinia';
import { ref } from 'vue';
import http from '@/shared/api/client';
import { useSseFeedStore } from '@/stores/sseFeed';

const POLL_MS = 5000;

export const useRiskCardsStore = defineStore('riskCards', () => {
  const riskStatus = ref<any>(null);
  const positions = ref<any[]>([]);
  const lastError = ref('');
  const lastHeartbeatTs = ref(0);

  let timer: number | null = null;
  let sseUnsub: (() => void) | null = null;
  let started = false;

  async function fetchRisk() {
    try {
      const { data } = await http.get('/api/portal/trader/api/dashboard/risk-status');
      riskStatus.value = data;
    } catch (e: any) {
      // 风控端点降级时保留上一次数据，仅记录错误（卡片不显示错误边界）
      lastError.value = e?.response?.data?.detail || e?.message || 'risk-status 拉取失败';
    }
  }

  async function fetchPositions() {
    try {
      const { data } = await http.get('/api/portal/trader/api/dashboard/positions');
      positions.value = Array.isArray(data) ? data : (data?.positions || []);
    } catch (e: any) {
      lastError.value = e?.response?.data?.detail || e?.message || 'positions 拉取失败';
    }
  }

  function poll() {
    void fetchRisk();
    void fetchPositions();
  }

  function start() {
    if (started) return;
    started = true;
    poll();
    timer = window.setInterval(poll, POLL_MS);
    const sse = useSseFeedStore();
    sseUnsub = sse.onEvent((data: any) => {
      if (data?.event === 'loop_heartbeat') {
        lastHeartbeatTs.value = data.ts || Date.now();
      }
    });
  }

  function stop() {
    started = false;
    if (timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
    sseUnsub?.();
    sseUnsub = null;
  }

  return { riskStatus, positions, lastError, lastHeartbeatTs, start, stop };
});
