<template>
  <header class="topbar">
    <button class="collapse-btn" @click="$emit('toggle-sidebar')">☰</button>
    <div class="crumb">{{ currentTitle }}</div>
    <div class="spacer" />
    <div class="mode-indicator" :class="portal.mode.toLowerCase()">
      <span class="dot" />
      {{ modeLabel }}
    </div>
    <div class="user-area">
      <span class="username">{{ auth.user?.display_name || auth.user?.username }}</span>
      <div class="avatar">{{ initial }}</div>
      <button class="btn btn-ghost" @click="onLogout">退出</button>
    </div>
  </header>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useAuthStore } from '@/stores/auth';
import { usePortalStore } from '@/stores/portal';

defineEmits<{ (e: 'toggle-sidebar'): void }>();

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const portal = usePortalStore();

const currentTitle = computed(() => (route.meta.title as string) || 'Hermes 中台');
const modeLabel = computed(() => {
  return {
    LIVE: '运行中（实盘）',
    SHADOW: '影子盘（不真实下单）',
    OFF: '已暂停',
    UNKNOWN: '状态未知',
  }[portal.mode] || '状态未知';
});
const initial = computed(() => (auth.user?.display_name || auth.user?.username || '?').charAt(0).toUpperCase());

function onLogout() {
  auth.logout();
  router.replace('/login');
}
</script>

<style scoped>
.topbar {
  height: 56px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  padding: 0 16px;
  gap: 14px;
  flex-shrink: 0;
}
.collapse-btn {
  background: none;
  border: none;
  color: var(--text);
  font-size: 18px;
  padding: 10px 12px;
  border-radius: 6px;
  cursor: pointer;
  min-width: 40px;
  min-height: 40px;
}
.collapse-btn:hover { background: var(--surface-2); }
.crumb {
  font-size: 15px;
  font-weight: 600;
}
.spacer { flex: 1; }
.mode-indicator {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  background: var(--surface-2);
  color: var(--muted);
}
.mode-indicator .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: currentColor;
}
.mode-indicator.live { color: var(--success); }
.mode-indicator.shadow { color: var(--warn); }
.mode-indicator.off { color: var(--muted); }
.mode-indicator.unknown { color: var(--muted); }
.user-area {
  display: flex;
  align-items: center;
  gap: 10px;
}
.username { font-size: 13px; color: var(--muted); }
.avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--accent);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 13px;
}

/* 移动端：隐藏用户名，收紧间距，模式标签缩短 */
@media (max-width: 768px) {
  .topbar {
    padding: 0 10px;
    gap: 8px;
  }
  .username {
    display: none;
  }
  .crumb {
    font-size: 14px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .user-area .btn {
    padding: 6px 10px;
  }
}
</style>
