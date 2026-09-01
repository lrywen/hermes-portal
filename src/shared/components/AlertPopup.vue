<template>
  <!--
    全局提醒弹窗容器。
    位置由后端 alert_config.popup_position 决定：
      bottom-right / bottom-left / top-right / center
    danger 级别需手动关闭，其余自动消失。
  -->
  <Teleport to="body">
    <div class="alert-layer" :class="positionClass">
      <transition-group name="popup">
        <div
          v-for="p in alerts.popups"
          :key="p.id"
          class="alert-popup"
          :class="p.level"
        >
          <div class="alert-head">
            <span class="alert-level-dot" />
            <span class="alert-title">{{ p.title }}</span>
            <button class="alert-close" @click="alerts.dismiss(p.id)">✕</button>
          </div>
          <div class="alert-body">{{ p.content }}</div>
          <div class="alert-foot">
            <span class="alert-time">{{ formatTime(p.ts) }}</span>
            <span class="alert-type">{{ eventLabel(p.type) }}</span>
          </div>
        </div>
      </transition-group>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useAlertStore } from '@/stores/alerts';

const alerts = useAlertStore();

const positionClass = computed(() => {
  const pos = alerts.config?.popup_position || 'bottom-right';
  return `pos-${pos}`;
});

function eventLabel(code: string): string {
  const found = alerts.eventCatalog.find((e) => e.code === code);
  return found?.name ?? code;
}

function formatTime(ts?: number): string {
  if (!ts) return '';
  const d = new Date(ts);
  return d.toLocaleTimeString('zh-CN', { hour12: false });
}
</script>

<style scoped>
.alert-layer {
  position: fixed;
  z-index: 9000;
  display: flex;
  flex-direction: column;
  gap: 12px;
  pointer-events: none;
  width: calc(100vw - 32px);
  max-width: 380px;
}
.alert-layer.pos-bottom-right { right: 16px; bottom: 80px; align-items: flex-end; }
.alert-layer.pos-bottom-left { left: 16px; bottom: 80px; align-items: flex-start; }
.alert-layer.pos-top-right { right: 16px; top: 80px; align-items: flex-end; }
.alert-layer.pos-center {
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  align-items: center;
}

.alert-popup {
  pointer-events: auto;
  width: 100%;
  box-sizing: border-box;
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: 4px solid var(--accent);
  border-radius: 10px;
  box-shadow: var(--shadow);
  padding: 14px 16px;
  animation: pop-in 0.25s ease;
}
.alert-popup.success { border-left-color: var(--success); }
.alert-popup.warn { border-left-color: var(--warn); }
.alert-popup.danger {
  border-left-color: var(--danger);
  animation: shake 0.4s;
}

.alert-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.alert-level-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent);
}
.alert-popup.success .alert-level-dot { background: var(--success); }
.alert-popup.warn .alert-level-dot { background: var(--warn); }
.alert-popup.danger .alert-level-dot { background: var(--danger); }
.alert-title {
  flex: 1;
  font-weight: 600;
  font-size: 14px;
}
.alert-close {
  background: none;
  border: none;
  color: var(--muted);
  cursor: pointer;
  font-size: 14px;
  padding: 6px 10px;
  margin: -6px -10px -6px 0;
  border-radius: 6px;
  line-height: 1;
  min-width: 36px;
  min-height: 36px;
}
.alert-close:hover { color: var(--text); background: var(--surface-2); }
.alert-body {
  font-size: 13px;
  color: var(--text);
  line-height: 1.5;
  white-space: pre-wrap;
}
.alert-foot {
  display: flex;
  justify-content: space-between;
  margin-top: 10px;
  font-size: 11px;
  color: var(--muted);
}

@keyframes pop-in {
  from { opacity: 0; transform: translateY(-10px) scale(0.96); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
@keyframes shake {
  0%, 100% { transform: translateX(0); }
  25% { transform: translateX(-6px); }
  75% { transform: translateX(6px); }
}
.popup-enter-active,
.popup-leave-active {
  transition: all 0.3s ease;
}
.popup-enter-from {
  opacity: 0;
  transform: translateX(60px);
}
.popup-leave-to {
  opacity: 0;
  transform: translateX(60px);
}
</style>
