<script setup lang="ts">
/**
 * 用户管理模块（RBAC）
 * - 用户列表、创建、启用/停用、角色分配、密码重置
 * - 所有写操作需要 admin:users 权限（后端校验）
 */
import { onMounted, reactive, ref } from 'vue';
import http from '@/shared/api/client';
import { useToast } from '@/stores/toast';
import type { User } from '@/shared/types';

const toast = useToast();
const users = ref<User[]>([]);
const loading = ref(false);
const showEditor = ref(false);
const saving = ref(false);

const ALL_ROLES = [
  { code: 'viewer', label: '观察者' },
  { code: 'trader', label: '交易员' },
  { code: 'operator', label: '运维员' },
  { code: 'admin', label: '管理员' },
];

const form = reactive({
  id: '' as string,
  username: '',
  display_name: '',
  password: '',
  roles: ['viewer'] as string[],
  is_active: true,
  isEdit: false,
});
const formError = ref('');

async function load() {
  loading.value = true;
  try {
    const { data } = await http.get<User[]>('/api/portal/users');
    users.value = data;
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '加载用户列表失败');
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  Object.assign(form, {
    id: '', username: '', display_name: '', password: '',
    roles: ['viewer'], is_active: true, isEdit: false,
  });
  formError.value = '';
  showEditor.value = true;
}

function openEdit(u: User) {
  Object.assign(form, {
    id: u.id, username: u.username, display_name: u.display_name,
    password: '', roles: [...u.roles], is_active: u.is_active, isEdit: true,
  });
  formError.value = '';
  showEditor.value = true;
}

function toggleRole(code: string) {
  const idx = form.roles.indexOf(code);
  if (idx >= 0) form.roles.splice(idx, 1);
  else form.roles.push(code);
}

async function submit() {
  formError.value = '';
  if (!form.isEdit && form.password.length < 6) {
    formError.value = '密码至少 6 位';
    return;
  }
  if (form.roles.length === 0) {
    formError.value = '至少选择一个角色';
    return;
  }
  saving.value = true;
  try {
    if (form.isEdit) {
      const payload: Record<string, any> = {
        display_name: form.display_name,
        roles: form.roles,
        is_active: form.is_active,
      };
      if (form.password) payload.password = form.password;
      await http.patch(`/api/portal/users/${form.id}`, payload);
      toast.ok('用户已更新');
    } else {
      await http.post('/api/portal/users', {
        username: form.username,
        password: form.password,
        display_name: form.display_name,
        roles: form.roles,
      });
      toast.ok('用户已创建');
    }
    showEditor.value = false;
    await load();
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '保存失败');
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="space-y-5">
    <header class="flex items-center justify-between">
      <div>
        <h2 class="text-xl font-semibold">用户与角色管理</h2>
        <p class="text-sm text-[var(--text-muted)] mt-1">基于 RBAC 模型，角色决定可访问的菜单与可执行的操作。</p>
      </div>
      <button class="btn btn-primary" @click="openCreate">➕ 新建用户</button>
    </header>

    <div v-if="loading" class="card text-center py-10 text-[var(--text-muted)]">加载中...</div>

    <div v-else class="card overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="text-left text-[var(--text-muted)] border-b border-[var(--border)]">
            <th class="py-2 px-3">用户名</th>
            <th class="py-2 px-3">显示名</th>
            <th class="py-2 px-3">角色</th>
            <th class="py-2 px-3">状态</th>
            <th class="py-2 px-3 text-right">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id" class="border-b border-[var(--border)] last:border-0 hover:bg-[var(--surface-hover)]">
            <td class="py-3 px-3 font-mono">{{ u.username }}</td>
            <td class="py-3 px-3">{{ u.display_name }}</td>
            <td class="py-3 px-3">
              <span v-for="r in u.roles" :key="r" class="badge-ok mr-1">{{ r }}</span>
            </td>
            <td class="py-3 px-3">
              <span :class="u.is_active ? 'badge-ok' : 'badge-danger'">{{ u.is_active ? '启用' : '停用' }}</span>
            </td>
            <td class="py-3 px-3 text-right">
              <button class="btn text-xs" @click="openEdit(u)">✏️ 编辑</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="showEditor" class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" @click.self="showEditor = false">
      <div class="card w-full max-w-md space-y-4">
        <h3 class="text-lg font-semibold">{{ form.isEdit ? `编辑用户 ${form.username}` : '新建用户' }}</h3>
        <div v-if="formError" class="badge-danger w-full">{{ formError }}</div>

        <div v-if="!form.isEdit">
          <label class="label block">用户名</label>
          <input class="input w-full" v-model="form.username" />
        </div>
        <div>
          <label class="label block">显示名</label>
          <input class="input w-full" v-model="form.display_name" />
        </div>
        <div>
          <label class="label block">{{ form.isEdit ? '新密码（留空表示不修改）' : '初始密码' }}</label>
          <input type="password" class="input w-full" v-model="form.password" autocomplete="new-password" />
        </div>
        <div>
          <label class="label block">角色（可多选）</label>
          <div class="flex flex-wrap gap-3">
            <label v-for="r in ALL_ROLES" :key="r.code" class="flex items-center gap-1 text-sm cursor-pointer">
              <input type="checkbox" :checked="form.roles.includes(r.code)" @change="toggleRole(r.code)" />
              {{ r.label }} <span class="text-xs text-[var(--text-muted)]">({{ r.code }})</span>
            </label>
          </div>
        </div>
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" v-model="form.is_active" /> 账号启用
        </label>

        <div class="flex justify-end gap-2 pt-2">
          <button class="btn" @click="showEditor = false">取消</button>
          <button class="btn btn-primary" :disabled="saving" @click="submit">{{ saving ? '保存中...' : '💾 保存' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>
