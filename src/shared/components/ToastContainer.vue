<template>
  <!-- 右下角 Toast 通知容器 -->
  <Teleport to="body">
    <div class="toast-container">
      <transition-group name="toast">
        <div
          v-for="m in toast.messages"
          :key="m.id"
          class="toast"
          :class="m.kind"
          @click="toast.dismiss(m.id)"
        >
          <span class="toast-icon">{{ icons[m.kind] }}</span>
          <span class="toast-text">{{ m.text }}</span>
        </div>
      </transition-group>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { useToastStore } from '@/stores/toast';
const toast = useToastStore();
const icons: Record<string, string> = { ok: '✓', err: '✕', info: 'ℹ' };
</script>

<style scoped>
.toast-container {
  position: fixed;
  right: 20px;
  bottom: 20px;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 10px;
  pointer-events: none;
}
.toast {
  pointer-events: auto;
  display: flex;
  align-items: center;
  gap: 10px;
  width: 320px;
  max-width: calc(100vw - 24px);
  padding: 12px 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: 3px solid var(--muted);
  border-radius: 8px;
  box-shadow: var(--shadow);
  cursor: pointer;
  font-size: 13px;
  box-sizing: border-box;
}
.toast.ok { border-left-color: var(--success); }
.toast.err { border-left-color: var(--danger); }
.toast.info { border-left-color: var(--accent); }
.toast-icon {
  font-weight: 700;
  width: 20px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  font-size: 12px;
}
.toast.ok .toast-icon { background: rgba(16, 185, 129, 0.15); color: var(--success); }
.toast.err .toast-icon { background: rgba(239, 68, 68, 0.15); color: var(--danger); }
.toast.info .toast-icon { background: rgba(59, 130, 246, 0.15); color: var(--accent); }
.toast-text { flex: 1; }

.toast-enter-active,
.toast-leave-active {
  transition: all 0.3s ease;
}
.toast-enter-from {
  opacity: 0;
  transform: translateX(40px);
}
.toast-leave-to {
  opacity: 0;
  transform: translateX(40px);
}
</style>
