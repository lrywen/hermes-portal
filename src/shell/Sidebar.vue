<template>
  <aside class="sidebar" :class="{ collapsed, mobile: isMobile, 'mobile-open': isMobile && mobileOpen }">
    <div class="brand">
      <span class="logo">⚡</span>
      <span v-if="showLabels" class="brand-text">Hermes 中台</span>
    </div>
    <nav class="menu">
      <template v-for="item in items" :key="item.id">
        <router-link
          v-if="item.path"
          :to="item.path"
          class="menu-item"
          :class="{ active: isActive(item.path!) }"
          @click="onNavigate"
        >
          <span class="mi-icon">{{ iconChar(item.icon) }}</span>
          <span v-if="showLabels" class="mi-label">{{ item.label }}</span>
        </router-link>
        <div v-else class="menu-group">
          <div v-if="showLabels" class="group-label">{{ item.label }}</div>
          <router-link
            v-for="child in item.children"
            :key="child.id"
            :to="child.path!"
            class="menu-item sub"
            :class="{ active: isActive(child.path!) }"
            :title="child.label"
            @click="onNavigate"
          >
            <span class="mi-icon">{{ iconChar(child.icon) }}</span>
            <span v-if="showLabels" class="mi-label">{{ child.label }}</span>
          </router-link>
        </div>
      </template>
    </nav>
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { usePortalStore } from '@/stores/portal';
import type { MenuItem } from '@/shared/types';

defineProps<{ collapsed: boolean; mobileOpen: boolean }>();

const portal = usePortalStore();
const route = useRoute();
const items = computed<MenuItem[]>(() => portal.menu);
// 移动端抽屉展开时始终显示标签；桌面端遵循折叠态
const isMobile = computed(() => portal.isMobile);
const showLabels = computed(() => (portal.isMobile ? true : !portal.sidebarCollapsed));

function isActive(path: string): boolean {
  return route.path === path || route.path.startsWith(path + '/');
}

// 移动端点击导航后自动关闭抽屉
function onNavigate() {
  if (portal.isMobile) portal.closeMobileSidebar();
}

// 简化版图标映射（用 unicode/emoji，避免引入图标库）
const ICONS: Record<string, string> = {
  LayoutDashboard: '📊',
  TrendingUp: '📈',
  Wallet: '💼',
  History: '🕐',
  BarChart3: '📉',
  Bot: '🤖',
  Cpu: '⚙️',
  Brain: '🧠',
  Radio: '📡',
  Settings: '🔧',
  ShieldAlert: '🛡️',
  Sliders: '🎛️',
  Coins: '🪙',
  BellRing: '🔔',
  Bell: '🔕',
  FileText: '📄',
  Lock: '🔒',
  Users: '👥',
  ScrollText: '📜',
};
function iconChar(name?: string): string {
  return name ? ICONS[name] || '•' : '•';
}
</script>

<style scoped>
.sidebar {
  width: 230px;
  background: var(--surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  transition: width 0.2s;
  flex-shrink: 0;
  overflow-y: auto;
}
.sidebar.collapsed {
  width: 60px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 18px;
  border-bottom: 1px solid var(--border);
  height: 56px;
  box-sizing: border-box;
}
.logo { font-size: 22px; }
.brand-text {
  font-weight: 700;
  font-size: 16px;
}
.menu {
  padding: 10px 8px;
  flex: 1;
}
.menu-group {
  margin-top: 8px;
}
.group-label {
  font-size: 11px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 8px 12px 4px;
}
.menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-radius: 8px;
  color: var(--text);
  font-size: 13px;
  margin-bottom: 2px;
  transition: background 0.12s;
  white-space: nowrap;
}
.menu-item:hover {
  background: var(--surface-2);
}
.menu-item.sub {
  padding-left: 20px;
}
.collapsed .menu-item {
  justify-content: center;
  padding: 9px;
}
.menu-item.active {
  background: rgba(59, 130, 246, 0.15);
  color: var(--accent-2);
}
.mi-icon {
  width: 20px;
  text-align: center;
  font-size: 15px;
}

/* 移动端抽屉：覆盖式侧边栏 + 滑入动画 */
@media (max-width: 768px) {
  .sidebar.mobile {
    position: fixed;
    top: 0;
    left: 0;
    bottom: 0;
    width: 260px !important;
    z-index: 9000;
    transform: translateX(-100%);
    transition: transform 0.22s ease;
    box-shadow: none;
  }
  .sidebar.mobile.mobile-open {
    transform: translateX(0);
    box-shadow: 4px 0 24px rgba(0, 0, 0, 0.4);
  }
}
</style>
