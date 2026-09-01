<template>
  <div class="login-wrap">
    <div class="login-card card">
      <div class="login-brand">
        <div class="logo">⚡</div>
        <h1>Hermes 中台</h1>
        <p>量化交易统一管控平台</p>
      </div>
      <form @submit.prevent="onSubmit">
        <label class="label">用户名</label>
        <input v-model="username" class="input" type="text" autocomplete="username" autofocus />
        <label class="label" style="margin-top: 14px">密码</label>
        <input v-model="password" class="input" type="password" autocomplete="current-password" />
        <p v-if="error" class="err">{{ error }}</p>
        <button class="btn btn-primary" style="width: 100%; margin-top: 20px" :disabled="loading">
          {{ loading ? '登录中…' : '登 录' }}
        </button>
      </form>
      <p class="hint">默认管理员 admin / admin123（首次登录请改密）</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';

const username = ref('admin');
const password = ref('');
const loading = ref(false);
const error = ref('');

const auth = useAuthStore();
const toast = useToastStore();
const router = useRouter();
const route = useRoute();

async function onSubmit() {
  if (!username.value || !password.value) {
    error.value = '请输入用户名和密码';
    return;
  }
  loading.value = true;
  error.value = '';
  try {
    await auth.login(username.value, password.value);
    toast.ok(`欢迎回来，${auth.user?.display_name || username.value}`);
    const redirect = (route.query.redirect as string) || '/overview';
    router.replace(redirect);
  } catch (e: any) {
    error.value = e.response?.data?.detail || '登录失败，请检查用户名和密码';
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.login-wrap {
  height: 100vh;
  height: 100dvh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background:
    radial-gradient(circle at 20% 20%, rgba(59, 130, 246, 0.15), transparent 50%),
    radial-gradient(circle at 80% 80%, rgba(16, 185, 129, 0.1), transparent 50%),
    var(--bg);
}
.login-card {
  width: 100%;
  max-width: 380px;
  padding: 36px 32px;
}
.login-brand {
  text-align: center;
  margin-bottom: 28px;
}
.logo {
  font-size: 40px;
}
h1 {
  margin: 8px 0 4px;
  font-size: 22px;
}
.login-brand p {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}
.err {
  color: var(--danger);
  font-size: 12px;
  margin: 12px 0 0;
}
.hint {
  text-align: center;
  color: var(--muted);
  font-size: 11px;
  margin-top: 18px;
}
@media (max-width: 480px) {
  .login-card {
    padding: 28px 20px;
  }
}
</style>
