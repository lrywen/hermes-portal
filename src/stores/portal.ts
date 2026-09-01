import { defineStore } from 'pinia';
import { ref } from 'vue';
import http from '@/shared/api/client';
import type { MenuItem } from '@/shared/types';

// 门户级 store：菜单、系统状态、运行模式、响应式布局
export const usePortalStore = defineStore('portal', () => {
  const menu = ref<MenuItem[]>([]);
  const mode = ref<'LIVE' | 'SHADOW' | 'OFF' | 'UNKNOWN'>('UNKNOWN');
  // 桌面端侧边栏折叠态（230px ↔ 60px）
  const sidebarCollapsed = ref(false);
  // 移动端断点（≤768px）下，侧边栏变为覆盖式抽屉
  const isMobile = ref(false);
  const mobileSidebarOpen = ref(false);

  async function loadMenu() {
    const { data } = await http.get('/api/portal/menu');
    menu.value = data.items;
  }

  async function loadMode() {
    try {
      const { data } = await http.get('/api/portal/trader/api/dashboard/operator/config');
      mode.value = data?.mode?.toUpperCase() || 'UNKNOWN';
    } catch {
      mode.value = 'UNKNOWN';
    }
  }

  // 初始化屏幕尺寸监听：移动端切换为抽屉式侧边栏
  function initResponsive() {
    if (typeof window === 'undefined') return;
    const mq = window.matchMedia('(max-width: 768px)');
    isMobile.value = mq.matches;
    const handler = (e: MediaQueryListEvent) => {
      isMobile.value = e.matches;
      // 回到桌面端时关闭抽屉，避免残留遮罩
      if (!e.matches) mobileSidebarOpen.value = false;
    };
    mq.addEventListener('change', handler);
  }

  // 顶栏汉堡按钮：移动端开关抽屉，桌面端切换折叠
  function toggleSidebar() {
    if (isMobile.value) mobileSidebarOpen.value = !mobileSidebarOpen.value;
    else sidebarCollapsed.value = !sidebarCollapsed.value;
  }

  function closeMobileSidebar() {
    mobileSidebarOpen.value = false;
  }

  return {
    menu,
    mode,
    sidebarCollapsed,
    isMobile,
    mobileSidebarOpen,
    loadMenu,
    loadMode,
    initResponsive,
    toggleSidebar,
    closeMobileSidebar,
  };
});
