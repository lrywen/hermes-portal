<script setup lang="ts">
/**
 * 审计日志查询模块
 * - 所有配置变更、用户登录、推送测试等关键操作均被后端记录
 * - 支持按 action 前缀、操作者、目标类型、时间范围过滤
 * - 支持展开查看 before/after 差异快照
 */
import { onMounted, ref } from 'vue';
import http from '@/shared/api/client';
import { useToast } from '@/stores/toast';
import type { AuditLog } from '@/shared/types';

const toast = useToast();

const items = ref<AuditLog[]>([]);
const total = ref(0);
const loading = ref(false);

const filters = ref({
  action: '',
  actor: '',
  target_type: '',
  days: 7,
  page: 1,
  page_size: 20,
});
const expanded = ref<Set<string>>(new Set());

const TARGET_LABEL: Record<string, string> = {
  push_channel: '推送渠道',
  alert_config: '提醒设置',
  custom_voice: '语音文件',
  user: '用户',
};

async function load() {
  loading.value = true;
  try {
    const { data } = await http.get('/api/portal/audit/logs', { params: filters.value });
    items.value = data.items;
    total.value = data.total;
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '加载审计日志失败');
  } finally {
    loading.value = false;
  }
}

function toggleExpand(id: string) {
  if (expanded.value.has(id)) expanded.value.delete(id);
  else expanded.value.add(id);
  // 触发响应式
  expanded.value = new Set(expanded.value);
}

function fmtTime(iso: string) {
  try {
    return new Date(iso).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return iso;
  }
}

const totalPages = () => Math.max(1, Math.ceil(total.value / filters.value.page_size));

onMounted(load);
</script>

<template>
  <div class="space-y-5">
    <header>
      <h2 class="text-xl font-semibold">审计日志</h2>
      <p class="text-sm text-[var(--text-muted)] mt-1">所有配置变更与敏感操作的不可抵赖记录，支持追溯。</p>
    </header>

    <!-- 过滤器 -->
    <div class="card grid grid-cols-2 md:grid-cols-5 gap-3 items-end">
      <div>
        <label class="label block">Action 前缀</label>
        <input class="input w-full" v-model="filters.action" placeholder="如 config.push" />
      </div>
      <div>
        <label class="label block">操作者</label>
        <input class="input w-full" v-model="filters.actor" placeholder="用户名" />
      </div>
      <div>
        <label class="label block">目标类型</label>
        <select class="input w-full" v-model="filters.target_type">
          <option value="">全部</option>
          <option v-for="(label, key) in TARGET_LABEL" :key="key" :value="key">{{ label }}</option>
        </select>
      </div>
      <div>
        <label class="label block">最近天数</label>
        <input type="number" min="1" max="90" class="input w-full" v-model.number="filters.days" />
      </div>
      <button class="btn btn-primary" @click="filters.page = 1; load()">🔍 查询</button>
    </div>

    <div v-if="loading" class="card text-center py-10 text-[var(--text-muted)]">加载中...</div>

    <div v-else class="card overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="text-left text-[var(--text-muted)] border-b border-[var(--border)]">
            <th class="py-2 px-3 w-8"></th>
            <th class="py-2 px-3">时间</th>
            <th class="py-2 px-3">操作者</th>
            <th class="py-2 px-3">动作</th>
            <th class="py-2 px-3">目标</th>
            <th class="py-2 px-3">摘要</th>
            <th class="py-2 px-3">IP</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in items" :key="row.id" class="border-b border-[var(--border)] last:border-0 hover:bg-[var(--surface-hover)]">
            <td class="py-2 px-3">
              <button v-if="row.before || row.after" class="text-xs" @click="toggleExpand(row.id)">
                {{ expanded.has(row.id) ? '▼' : '▶' }}
              </button>
            </td>
            <td class="py-2 px-3 whitespace-nowrap text-xs">{{ fmtTime(row.created_at) }}</td>
            <td class="py-2 px-3 font-mono text-xs">{{ row.actor_username || '系统' }}</td>
            <td class="py-2 px-3"><span class="badge-muted font-mono text-xs">{{ row.action }}</span></td>
            <td class="py-2 px-3 text-xs">
              <span v-if="row.target_type">{{ TARGET_LABEL[row.target_type] || row.target_type }}</span>
              <span v-if="row.target_id" class="text-[var(--text-muted)] ml-1">#{{ row.target_id.slice(0, 8) }}</span>
            </td>
            <td class="py-2 px-3">{{ row.summary }}</td>
            <td class="py-2 px-3 text-xs text-[var(--text-muted)] font-mono">{{ row.ip || '—' }}</td>
          </tr>
          <template v-for="row in items" :key="'d-' + row.id">
            <tr v-if="expanded.has(row.id)">
            <td colspan="7" class="bg-[var(--surface-hover)] px-6 py-3">
              <div class="grid md:grid-cols-2 gap-4 text-xs">
                <div>
                  <div class="text-[var(--text-muted)] mb-1">变更前（before）</div>
                  <pre class="bg-black/40 p-2 rounded overflow-x-auto">{{ JSON.stringify(row.before, null, 2) || '—' }}</pre>
                </div>
                <div>
                  <div class="text-[var(--text-muted)] mb-1">变更后（after）</div>
                  <pre class="bg-black/40 p-2 rounded overflow-x-auto">{{ JSON.stringify(row.after, null, 2) || '—' }}</pre>
                </div>
              </div>
            </td>
          </tr>
          </template>
        </tbody>
      </table>

      <!-- 分页 -->
      <div class="flex items-center justify-between p-3 text-sm">
        <span class="text-[var(--text-muted)]">共 {{ total }} 条，第 {{ filters.page }} / {{ totalPages() }} 页</span>
        <div class="flex gap-2">
          <button class="btn text-xs" :disabled="filters.page <= 1" @click="filters.page--; load()">上一页</button>
          <button class="btn text-xs" :disabled="filters.page >= totalPages()" @click="filters.page++; load()">下一页</button>
        </div>
      </div>
    </div>
  </div>
</template>
