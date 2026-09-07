import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router';
import { useAuthStore } from '@/stores/auth';

const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('@/auth/LoginPage.vue'), meta: { public: true } },
  {
    path: '/',
    component: () => import('@/shell/AppShell.vue'),
    redirect: '/overview',
    children: [
      { path: 'overview', name: 'overview', component: () => import('@/modules/dashboard/Overview.vue'), meta: { title: '总览', perm: 'dashboard:read' } },
      { path: 'positions', name: 'positions', component: () => import('@/modules/trading/Positions.vue'), meta: { title: '持仓', perm: 'positions:read' } },
      { path: 'trades', name: 'trades', component: () => import('@/modules/trading/Trades.vue'), meta: { title: '交易历史', perm: 'trades:read' } },
      { path: 'analysis', name: 'analysis', component: () => import('@/modules/trading/Analysis.vue'), meta: { title: '深度分析', perm: 'analysis:read' } },
      { path: 'agents', name: 'agents', component: () => import('@/modules/agents/Agents.vue'), meta: { title: '智能体编排', perm: 'agent:control' } },
      { path: 'channels', name: 'channels', component: () => import('@/modules/agents/Channels.vue'), meta: { title: '渠道消息', perm: 'channels:read' } },
      { path: 'hta-research', name: 'hta-research', component: () => import('@/modules/hta/HtaResearch.vue'), meta: { title: '多视角研判', perm: 'agent:research' } },
      { path: 'operator', name: 'operator', component: () => import('@/modules/operations/Operator.vue'), meta: { title: '操作员控制台', perm: 'operator:mode' } },
      // Audit 2026-09-07 (M3): 影子臂评级中心，评级含闸门姿态/盲信号 → operator:mode
      { path: 'risk-arms', name: 'risk-arms', component: () => import('@/modules/operations/RiskArms.vue'), meta: { title: '影子臂评级中心', perm: 'operator:mode' } },
      { path: 'config', name: 'config', component: () => import('@/modules/operations/Config.vue'), meta: { title: '系统配置', perm: 'config:read' } },
      { path: 'postmortems', name: 'postmortems', component: () => import('@/modules/operations/Postmortems.vue'), meta: { title: '复盘报告', perm: 'postmortems:read' } },
      { path: 'push', name: 'push', component: () => import('@/modules/push/PushSettings.vue'), meta: { title: '推送设置', perm: 'push:manage' } },
      { path: 'alerts', name: 'alerts', component: () => import('@/modules/alerts/AlertSettings.vue'), meta: { title: '提醒设置', perm: 'alert:manage' } },
      { path: 'users', name: 'users', component: () => import('@/modules/system/Users.vue'), meta: { title: '用户管理', perm: 'admin:users' } },
      { path: 'audit', name: 'audit', component: () => import('@/modules/system/AuditLog.vue'), meta: { title: '审计日志', perm: 'admin:audit' } },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/overview' },
];

const router = createRouter({
  history: createWebHistory('/portal/'),
  routes,
});

router.beforeEach(async (to) => {
  const auth = useAuthStore();
  if (to.meta.public) return true;
  if (!auth.isAuthenticated) {
    // M-12：access token 仅驻留内存，刷新页面后丢失；先用 httpOnly Cookie
    // 中的 refresh token 静默恢复会话（整个页面生命周期只尝试一次），
    // 失败再跳登录页。
    const restored = await auth.restoreSession();
    if (!restored) {
      return { path: '/login', query: { redirect: to.fullPath } };
    }
  }
  if (!auth.user) {
    await auth.fetchMe();
    // fetchMe 失败（token 过期/无效）会清空会话，此时必须跳登录，避免停在受保护页黑屏
    if (!auth.user) {
      return { path: '/login', query: { redirect: to.fullPath } };
    }
  }
  const perm = to.meta.perm as string | undefined;
  if (perm && !auth.hasPermission(perm)) {
    return { path: '/overview' };
  }
  return true;
});

export default router;
