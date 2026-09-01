<template>
  <div class="app-shell">
    <Sidebar :collapsed="portal.sidebarCollapsed" :mobile-open="portal.mobileSidebarOpen" />
    <!-- 移动端抽屉遮罩：点击关闭 -->
    <div
      v-if="portal.isMobile && portal.mobileSidebarOpen"
      class="sidebar-overlay"
      @click="portal.closeMobileSidebar()"
    />
    <div class="main-col">
      <TopBar @toggle-sidebar="portal.toggleSidebar()" />
      <main class="content">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </main>
    </div>
    <ToastContainer />
    <AlertPopup />
    <!-- SSE 实时连接状态指示灯：绿色=已连接，黄色=重连中，红色=已断开；点击展开诊断 -->
    <div class="sse-widget" :class="{ open: diagOpen }">
      <div
        class="sse-status"
        :class="statusDotClass"
        :title="statusTitleText"
        @click="diagOpen = !diagOpen"
      >
        <span class="dot" />
        <span class="label">{{ statusLabelText }}</span>
        <span class="chev">{{ diagOpen ? '▾' : '▸' }}</span>
      </div>
      <div v-if="diagOpen" class="sse-diag">
        <div class="diag-row feed-row">
          <span class="feed-tag">
            <span class="feed-dot" :class="feedStatusClass" />
            行情馈送
          </span>
          <span>{{ feedStatusLabel }}</span>
        </div>
        <div class="diag-row">
          <span>累计事件</span><span>{{ sseFeed.eventCount }}</span>
        </div>
        <div class="diag-row">
          <span>最后消息距今</span><span>{{ lastMessageAge }}</span>
        </div>
        <div class="diag-section">最近收到：</div>
        <pre v-if="sseFeed.lastEvent" class="diag-pre">{{ lastEventText }}</pre>
        <div v-else class="diag-empty">（暂无）</div>
        <div class="diag-section">桥接结果：</div>
        <pre v-if="sseFeed.lastBridge" class="diag-pre">{{ lastBridgeText }}</pre>
        <div v-else class="diag-empty">（暂无）</div>
        <label class="diag-toggle">
          <input
            type="checkbox"
            :checked="sseFeed.heartbeatSoundEnabled"
            @change="(e) => sseFeed.setHeartbeatSound((e.target as HTMLInputElement).checked)"
          />
          <span>scan 心跳提示音</span>
          <button
            class="diag-test-beep"
            @click.stop="sseFeed.playBeep()"
            :disabled="!sseFeed.heartbeatSoundEnabled"
            title="试听提示音"
          >
            ▶ 试听
          </button>
        </label>
        <button class="diag-reconnect" @click="sseFeed.manualReconnect()">
          手动重连
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onBeforeUnmount, computed, ref } from 'vue';
import Sidebar from './Sidebar.vue';
import TopBar from './TopBar.vue';
import ToastContainer from '@/shared/components/ToastContainer.vue';
import AlertPopup from '@/shared/components/AlertPopup.vue';
import { useAuthStore } from '@/stores/auth';
import { usePortalStore } from '@/stores/portal';
import { useAlertStore } from '@/stores/alerts';
import { useSseFeedStore } from '@/stores/sseFeed';

const auth = useAuthStore();
const portal = usePortalStore();
const alerts = useAlertStore();
const sseFeed = useSseFeedStore();

// SSE 连接状态指示灯
const sseStatusClass = computed(() => ({
  connected: sseFeed.connected,
  reconnecting: sseFeed.reconnecting,
  disconnected: !sseFeed.connected && !sseFeed.reconnecting,
}));
const sseStatusLabel = computed(() => {
  if (sseFeed.connected) return '实时已连接';
  if (sseFeed.reconnecting) return '实时重连中…';
  return '实时未连接';
});
const sseStatusTitle = computed(() => '点击展开实时事件诊断');

// Phase 4 P0-3：行情馈送状态（trader 端 ws_status 边沿事件，后端已做 30s 滞回）。
// 与 SSE 连接灯分离：SSE 灯反映浏览器↔BFF 的 EventSource；馈送灯反映 trader←交易所
// 的 WS/REST 数据链路。unknown=本会话尚未收到 ws_status（事件流被 SSE 白名单过滤时
// 恒为 unknown，不报警）。
type FeedState = 'unknown' | 'ok' | 'degraded' | 'down';
const feedStatus = ref<FeedState>('unknown');
const feedStatusClass = computed(() => ({
  'feed-ok': feedStatus.value === 'ok',
  'feed-warn': feedStatus.value === 'degraded',
  'feed-danger': feedStatus.value === 'down',
  'feed-unknown': feedStatus.value === 'unknown',
}));
const feedStatusLabel = computed(() => {
  switch (feedStatus.value) {
    case 'ok': return 'WS 实时正常';
    case 'degraded': return '降级（REST 轮询）';
    case 'down': return '中断（已停止开新仓）';
    default: return '未知';
  }
});
// 主灯取两者中更严重的状态：馈送 down 红色、degraded 黄色，即使 SSE 连接本身正常。
const statusDotClass = computed(() => {
  if (sseStatusClass.value.disconnected || feedStatus.value === 'down') {
    return { disconnected: true };
  }
  if (sseStatusClass.value.reconnecting || feedStatus.value === 'degraded') {
    return { reconnecting: true };
  }
  return { connected: true };
});
// 主灯文案：馈送异常优先展示（它是交易能力风险），否则展示 SSE 连接态。
const statusLabelText = computed(() => {
  if (feedStatus.value === 'down') return '馈送中断·停开仓';
  if (feedStatus.value === 'degraded') return '馈送降级·REST';
  return sseStatusLabel.value;
});
const statusTitleText = computed(() => {
  const feed = `行情馈送：${feedStatusLabel.value}`;
  const sse = sseStatusLabel.value;
  return `${feed}｜实时通道：${sse}（点击展开诊断）`;
});
let unsubFeedStatus: (() => void) | null = null;

// 诊断面板
const diagOpen = ref(false);
const now = ref(Date.now());
let tickTimer: number | null = null;
onMounted(() => {
  tickTimer = window.setInterval(() => (now.value = Date.now()), 1000);
  unsubFeedStatus = sseFeed.onEvent((data: any) => {
    if (!data || data.event !== 'ws_status') return;
    const s = String(data.status ?? '').toLowerCase();
    if (s === 'ok' || s === 'degraded' || s === 'down') {
      feedStatus.value = s;
    }
  });
});
onBeforeUnmount(() => {
  if (tickTimer) clearInterval(tickTimer);
  if (unsubFeedStatus) { unsubFeedStatus(); unsubFeedStatus = null; }
});
const lastMessageAge = computed(() => {
  if (!sseFeed.lastMessageTs) return '—';
  return ((now.value - sseFeed.lastMessageTs) / 1000).toFixed(1) + 's';
});
const lastEventText = computed(() => {
  const e = sseFeed.lastEvent;
  if (!e) return '';
  return `event: ${e.event}\n年龄: ${(e.age / 1000).toFixed(1)}s\n原始: ${e.raw}`;
});
const lastBridgeText = computed(() => {
  const b = sseFeed.lastBridge;
  if (!b) return '';
  return `event: ${b.event}\n年龄: ${(b.age / 1000).toFixed(1)}s\n结果: ${b.action}${b.mapped ? '\n映射: ' + b.mapped : ''}`;
});

onMounted(async () => {
  portal.initResponsive();
  try {
    await Promise.all([
      auth.fetchMe(),
      portal.loadMenu(),
      portal.loadMode(),
      alerts.loadConfig(),
      alerts.loadEvents(),
    ]);
    // 登录后全程保持单条 SSE 连接，使任意页面都能收到实时事件并触发全局提醒弹窗/语音
    if (auth.isAuthenticated) {
      sseFeed.start();
    }
  } catch (e) {
    // 401 等鉴权失败已由 axios 拦截器统一跳转登录页；此处仅避免未捕获 Promise  rejection
    console.warn('[AppShell] initial data load failed', e);
  }
});
</script>

<style scoped>
.app-shell {
  display: flex;
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
}
.main-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.content {
  flex: 1;
  overflow-y: auto;
  padding: 22px;
  background: var(--bg);
}
.sidebar-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 8500;
  -webkit-tap-highlight-color: transparent;
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
.sse-widget {
  position: fixed;
  right: 18px;
  bottom: 18px;
  z-index: 8000;
}
.sse-status {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  background: rgba(20, 22, 30, 0.85);
  backdrop-filter: blur(6px);
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: var(--text-secondary);
  cursor: pointer;
  user-select: none;
  transition: opacity 0.2s ease;
}
.sse-status:hover {
  opacity: 0.85;
}
.sse-status .chev {
  font-size: 10px;
  opacity: 0.6;
}
.sse-diag {
  margin-top: 8px;
  width: 340px;
  max-width: 80vw;
  padding: 12px;
  border-radius: 10px;
  background: rgba(15, 17, 23, 0.96);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: var(--text-secondary);
  font-size: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
}
.diag-row {
  display: flex;
  justify-content: space-between;
  padding: 2px 0;
}
.feed-row {
  padding: 4px 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  margin-bottom: 4px;
}
.feed-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.feed-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.feed-dot.feed-ok {
  background: #22c55e;
  box-shadow: 0 0 5px #22c55e;
}
.feed-dot.feed-warn {
  background: #f59e0b;
  box-shadow: 0 0 5px #f59e0b;
}
.feed-dot.feed-danger {
  background: #ef4444;
  box-shadow: 0 0 5px #ef4444;
  animation: sse-pulse 1s infinite;
}
.feed-dot.feed-unknown {
  background: #6b7280;
}
.diag-section {
  margin-top: 10px;
  font-weight: 600;
  color: var(--text-primary);
  opacity: 0.8;
}
.diag-pre {
  margin: 4px 0 0;
  padding: 8px;
  background: rgba(0, 0, 0, 0.35);
  border-radius: 6px;
  font-family: ui-monospace, monospace;
  font-size: 11px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 140px;
  overflow-y: auto;
}
.diag-empty {
  margin-top: 4px;
  opacity: 0.5;
}
.diag-reconnect {
  margin-top: 10px;
  width: 100%;
  padding: 6px;
  border-radius: 6px;
  border: 1px solid rgba(255, 255, 255, 0.15);
  background: rgba(255, 255, 255, 0.06);
  color: var(--text-primary);
  font-size: 12px;
  cursor: pointer;
}
.diag-reconnect:hover {
  background: rgba(255, 255, 255, 0.12);
}
.diag-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  padding: 6px 8px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.04);
  cursor: pointer;
  font-size: 12px;
}
.diag-toggle input[type='checkbox'] {
  margin: 0;
  cursor: pointer;
}
.diag-toggle span {
  flex: 1;
}
.diag-test-beep {
  padding: 3px 8px;
  border-radius: 4px;
  border: 1px solid rgba(255, 255, 255, 0.15);
  background: rgba(255, 255, 255, 0.06);
  color: var(--text-primary);
  font-size: 11px;
  cursor: pointer;
}
.diag-test-beep:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.14);
}
.diag-test-beep:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.sse-status .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.sse-status.connected .dot {
  background: #22c55e;
  box-shadow: 0 0 6px #22c55e;
}
.sse-status.reconnecting .dot {
  background: #f59e0b;
  box-shadow: 0 0 6px #f59e0b;
  animation: sse-pulse 1s infinite;
}
.sse-status.disconnected .dot {
  background: #ef4444;
  box-shadow: 0 0 6px #ef4444;
}
@keyframes sse-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

/* 移动端适配 */
@media (max-width: 768px) {
  .content {
    padding: 14px 12px;
    /* 底部留出 SSE 浮层空间，避免内容被遮挡 */
    padding-bottom: 64px;
  }
  .sse-widget {
    right: 10px;
    bottom: 10px;
  }
  .sse-status .label {
    display: none;
  }
}
</style>
