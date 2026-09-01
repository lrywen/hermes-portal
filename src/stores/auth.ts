import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import http from '@/shared/api/client';
import type { User } from '@/shared/types';

// M-12（补充审计 2026-08-30）：token 不再写入 localStorage。
// - access token（15 分钟）仅驻留内存：刷新页面即失效，XSS 无法持久窃取；
// - refresh token 由后端经 httpOnly Cookie 下发（JS 不可读），页面刷新后
//   靠 canRefresh 标记触发静默刷新恢复会话；Cookie 由浏览器自动随同源请求携带。
export const useAuthStore = defineStore('auth', () => {
  // access token 仅存内存，刷新页面后为 null
  const accessToken = ref<string | null>(null);
  // 本次会话是否拥有 refresh Cookie（登录成功置 true，登出置 false）。
  // 刷新页面后为 false，路由守卫会先尝试一次静默 refresh 来恢复会话。
  const canRefresh = ref(false);
  // 页面加载后的一次性静默刷新（防止并发重复请求）
  let restorePromise: Promise<boolean> | null = null;
  const user = ref<User | null>(null);

  const isAuthenticated = computed(() => !!accessToken.value);
  const permissions = computed(() => new Set(user.value?.permissions ?? []));
  const roles = computed(() => new Set(user.value?.roles ?? []));

  function hasPermission(...perms: string[]): boolean {
    return perms.every((p) => permissions.value.has(p));
  }
  function hasAnyPermission(...perms: string[]): boolean {
    return perms.some((p) => permissions.value.has(p));
  }

  async function login(username: string, password: string) {
    const { data } = await http.post('/api/portal/auth/login', { username, password });
    accessToken.value = data.access_token;
    canRefresh.value = true;
    user.value = data.user;
  }

  async function fetchMe() {
    if (!accessToken.value) return;
    try {
      const { data } = await http.get('/api/portal/auth/me');
      user.value = data;
    } catch {
      logout();
    }
  }

  /**
   * 用 httpOnly Cookie 中的 refresh token 换取新的 access token。
   * 成功返回新 token；无 Cookie 或刷新失败返回 null。
   */
  async function refresh(): Promise<string | null> {
    try {
      const { data } = await http.post('/api/portal/auth/refresh', {});
      accessToken.value = data.access_token;
      canRefresh.value = true;
      return data.access_token;
    } catch {
      canRefresh.value = false;
      return null;
    }
  }

  /**
   * 页面刷新/硬导航后恢复会话：无内存 access token 时，用 refresh Cookie
   * 静默换取新 access token 并加载用户信息。整个页面生命周期只尝试一次，
   * 失败则保持未登录态（路由守卫会跳登录页）。
   */
  async function restoreSession(): Promise<boolean> {
    if (accessToken.value) return true;
    if (!restorePromise) {
      restorePromise = (async () => {
        const token = await refresh();
        if (!token) return false;
        try {
          const { data } = await http.get('/api/portal/auth/me');
          user.value = data;
          return true;
        } catch {
          logout();
          return false;
        }
      })();
    }
    return restorePromise;
  }

  async function logout() {
    accessToken.value = null;
    canRefresh.value = false;
    user.value = null;
    try {
      // 通知后端清除 httpOnly refresh Cookie（失败不影响前端登出）
      await http.post('/api/portal/auth/logout', {});
    } catch {
      // 忽略：Cookie 过期后自然失效
    }
  }

  return {
    accessToken,
    canRefresh,
    user,
    isAuthenticated,
    permissions,
    roles,
    hasPermission,
    hasAnyPermission,
    login,
    fetchMe,
    refresh,
    restoreSession,
    logout,
  };
});
