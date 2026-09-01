import axios, { type AxiosInstance } from 'axios';
import { useAuthStore } from '@/stores/auth';
import router from '@/router';

// 统一 API 客户端：自动注入 JWT、401 自动刷新、错误统一抛出
const http: AxiosInstance = axios.create({
  baseURL: '/',
  timeout: 30000,
});

// 认证类端点本身的 401 不得触发自动刷新/登出跳转，否则 refresh 失败会递归调用自身
const AUTH_URLS = ['/api/portal/auth/login', '/api/portal/auth/refresh', '/api/portal/auth/logout'];
function isAuthUrl(url?: string): boolean {
  return !!url && AUTH_URLS.some((u) => url.includes(u));
}

http.interceptors.request.use((config) => {
  const auth = useAuthStore();
  if (auth.accessToken) {
    config.headers.Authorization = `Bearer ${auth.accessToken}`;
  }
  return config;
});

let refreshing: Promise<string | null> | null = null;

function redirectToLogin() {
  // 避免在登录页本身重复跳转
  if (router.currentRoute.value.path === '/login') return;
  router.replace({
    path: '/login',
    query: { redirect: router.currentRoute.value.fullPath },
  });
}

http.interceptors.response.use(
  (res) => res,
  async (error) => {
    const auth = useAuthStore();
    const original = error.config;
    const status = error.response?.status;
    if (status === 401 && !original?._retry && !isAuthUrl(original?.url) && auth.canRefresh) {
      // access token 过期：用 httpOnly Cookie 中的 refresh token 静默换新后重试一次
      original._retry = true;
      try {
        if (!refreshing) {
          refreshing = auth.refresh();
        }
        const newToken = await refreshing;
        refreshing = null;
        if (newToken) {
          original.headers.Authorization = `Bearer ${newToken}`;
          return http(original);
        }
        // refresh 失败（Cookie 缺失/过期）→ 登出并跳登录
        await auth.logout();
        redirectToLogin();
      } catch {
        refreshing = null;
        await auth.logout();
        redirectToLogin();
      }
    } else if (status === 401 && !original?._retry && !isAuthUrl(original?.url)) {
      // 没有 refresh 能力，直接登出并跳登录
      await auth.logout();
      redirectToLogin();
    }
    return Promise.reject(error);
  }
);

export default http;
