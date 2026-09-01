<script setup lang="ts">
/**
 * 推送设置模块
 * - 飞书 / 钉钉 / 企业微信 / Telegram / 自定义 Webhook 五类渠道
 * - 每个渠道独立启用开关，配置项持久化到后端
 * - 敏感字段（webhook_url / bot_token）脱敏显示，"******" 表示保留原值
 * - 提供「测试推送」即时验证渠道连通性
 * - 所有写操作走 /api/portal/push/*，后端自动记录审计日志
 */
import { onMounted, reactive, ref } from 'vue';
import http from '@/shared/api/client';
import { useToast } from '@/stores/toast';
import type { PushChannel } from '@/shared/types';

const toast = useToast();

// 渠道元信息：用于渲染图标、字段标签与占位提示
const CHANNEL_META: Record<string, { label: string; icon: string; fields: { key: string; label: string; type: string; placeholder?: string; secret?: boolean }[] }> = {
  feishu: {
    label: '飞书',
    icon: '🦅',
    fields: [
      { key: 'webhook_url', label: 'Webhook 地址', type: 'text', placeholder: 'https://open.feishu.cn/open-apis/bot/v2/hook/xxx', secret: true },
      { key: 'signing_key', label: '签名校验密钥（可选）', type: 'text', placeholder: '启用签名校验时填写', secret: true },
    ],
  },
  dingtalk: {
    label: '钉钉',
    icon: '🔔',
    fields: [
      { key: 'webhook_url', label: 'Webhook 地址', type: 'text', placeholder: 'https://oapi.dingtalk.com/robot/send?access_token=xxx', secret: true },
      { key: 'signing_key', label: '加签密钥（可选）', type: 'text', placeholder: 'SEC...', secret: true },
    ],
  },
  wecom: {
    label: '企业微信',
    icon: '💬',
    fields: [
      { key: 'webhook_url', label: 'Webhook 地址', type: 'text', placeholder: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx', secret: true },
    ],
  },
  telegram: {
    label: 'Telegram',
    icon: '✈️',
    fields: [
      { key: 'bot_token', label: 'Bot Token', type: 'text', placeholder: '123456:ABC-DEF...', secret: true },
      { key: 'chat_id', label: 'Chat ID', type: 'text', placeholder: '例如 -1001234567890' },
    ],
  },
  custom_webhook: {
    label: '自定义 Webhook',
    icon: '🔗',
    fields: [
      { key: 'webhook_url', label: 'Webhook 地址', type: 'text', placeholder: 'https://your-domain.com/hook', secret: true },
      { key: 'method', label: '请求方法', type: 'text', placeholder: 'POST（默认）' },
    ],
  },
};

const channels = ref<PushChannel[]>([]);
const loading = ref(false);
const saving = ref<string | null>(null);
const testing = ref<string | null>(null);

// 每个渠道的编辑表单（独立草稿，避免相互影响）
const drafts = reactive<Record<string, Record<string, any>>>({});

async function load() {
  loading.value = true;
  try {
    const { data } = await http.get<PushChannel[]>('/api/portal/push/channels');
    channels.value = data;
    // 初始化草稿：把脱敏的敏感字段标记为 "******"
    for (const ch of data) {
      drafts[ch.channel] = {
        name: ch.name,
        enabled: ch.enabled,
        config: { ...(ch.config || {}) },
      };
    }
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '加载推送渠道失败');
  } finally {
    loading.value = false;
  }
}

/** 保存单个渠道；启用前后端会二次校验必填字段 */
async function save(channel: string) {
  saving.value = channel;
  try {
    const draft = drafts[channel];
    const payload = {
      name: draft.name,
      enabled: draft.enabled,
      config: draft.config,
    };
    await http.put(`/api/portal/push/channels/${channel}`, payload);
    toast.ok(`${CHANNEL_META[channel]?.label || channel} 配置已保存`);
    await load();
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '保存失败');
  } finally {
    saving.value = null;
  }
}

async function test(channel: string) {
  testing.value = channel;
  try {
    await http.post('/api/portal/push/test', { channel });
    toast.ok('测试消息已发送，请在对应渠道查收');
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '推送失败');
  } finally {
    testing.value = null;
  }
}

onMounted(load);
</script>

<template>
  <div class="space-y-5">
    <header class="flex items-center justify-between">
      <div>
        <h2 class="text-xl font-semibold">推送终端配置</h2>
        <p class="text-sm text-[var(--text-muted)] mt-1">
          管理飞书、钉钉、企业微信、Telegram 等推送渠道。敏感字段以 <code>******</code> 显示，留此值表示不修改原配置。
        </p>
      </div>
      <button class="btn" :disabled="loading" @click="load">🔄 刷新</button>
    </header>

    <div v-if="loading" class="card text-center text-[var(--text-muted)] py-10">加载中...</div>

    <div class="grid gap-4 lg:grid-cols-2">
      <div v-for="ch in channels" :key="ch.channel" class="card space-y-4">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-3">
            <span class="text-2xl">{{ CHANNEL_META[ch.channel]?.icon || '📡' }}</span>
            <div>
              <div class="font-semibold">{{ CHANNEL_META[ch.channel]?.label || ch.channel }}</div>
              <div class="text-xs text-[var(--text-muted)]">标识：{{ ch.channel }}</div>
            </div>
          </div>
          <!-- 启用/关闭开关 -->
          <label class="flex items-center gap-2 cursor-pointer select-none">
            <span class="text-xs" :class="drafts[ch.channel]?.enabled ? 'text-emerald-400' : 'text-[var(--text-muted)]'">
              {{ drafts[ch.channel]?.enabled ? '已启用' : '已关闭' }}
            </span>
            <input type="checkbox" class="sr-only peer" v-model="drafts[ch.channel].enabled" />
            <span class="w-10 h-6 rounded-full bg-[var(--border)] peer-checked:bg-emerald-500 relative transition">
              <span class="absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition peer-checked:translate-x-4"></span>
            </span>
          </label>
        </div>

        <label class="label block">显示名称</label>
        <input class="input w-full" v-model="drafts[ch.channel].name" />

        <template v-for="f in CHANNEL_META[ch.channel]?.fields || []" :key="f.key">
          <label class="label block">{{ f.label }}</label>
          <input
            class="input w-full"
            :type="f.secret ? 'password' : f.type"
            :placeholder="f.placeholder"
            v-model="drafts[ch.channel].config[f.key]"
          />
        </template>

        <div class="flex gap-2 pt-2">
          <button class="btn btn-primary flex-1" :disabled="saving === ch.channel" @click="save(ch.channel)">
            {{ saving === ch.channel ? '保存中...' : '💾 保存配置' }}
          </button>
          <button class="btn" :disabled="testing === ch.channel || !drafts[ch.channel]?.enabled" @click="test(ch.channel)">
            {{ testing === ch.channel ? '发送中...' : '📨 测试' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
