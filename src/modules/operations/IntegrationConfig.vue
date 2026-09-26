<script setup lang="ts">
/**
 * 集成配置：LLM 模型 / 交易所 API / 飞书
 * - 三个独立视觉区域（Tab），互不干扰
 * - LLM 支持多 profile 的新增 / 编辑 / 删除 / 设默认（多实例）
 * - 交易所、飞书为单实例整块编辑
 * - 密钥字段只显示掩码；编辑留空=不修改，重填=整体替换
 * - 写操作需 config:write，无权限的账号只读
 * - 所有操作通过 toast 给出明确成功/失败反馈
 */
import { computed, onMounted, reactive, ref } from 'vue';
import http from '@/shared/api/client';
import { useToast } from '@/stores/toast';
import { useConfirmStore } from '@/stores/confirm';
import { useAuthStore } from '@/stores/auth';

const toast = useToast();
const confirmStore = useConfirmStore();
const auth = useAuthStore();

const BASE = '/api/portal/trader/api/dashboard/secrets';
const canWrite = computed(() => auth.hasPermission('config:write'));

const loading = ref(true);
const activeTab = ref<'llm' | 'exchange' | 'feishu'>('llm');

// ── LLM ────────────────────────────────────────────────────────────────
interface LlmProfile {
  id: string;
  name: string;
  base_url: string;
  api_key: string; // 掩码
  model: string;
  temperature: number;
  max_tokens: number;
  timeout_sec: number;
  enabled: boolean;
  is_default: boolean;
}
const profiles = ref<LlmProfile[]>([]);
const defaultProfileId = ref<string | null>(null);

// 弹窗（新增/编辑）
const showEditor = ref(false);
const editorMode = ref<'create' | 'edit'>('create');
const editingId = ref('');
const editorSaving = ref(false);
const form = reactive({
  name: '',
  base_url: '',
  api_key: '',
  model: '',
  temperature: 0.1,
  max_tokens: 500,
  timeout_sec: 25,
  enabled: true,
});

function resetForm() {
  form.name = '';
  form.base_url = '';
  form.api_key = '';
  form.model = '';
  form.temperature = 0.1;
  form.max_tokens = 500;
  form.timeout_sec = 25;
  form.enabled = true;
}

function openCreate() {
  editorMode.value = 'create';
  editingId.value = '';
  resetForm();
  showEditor.value = true;
}

function openEdit(p: LlmProfile) {
  editorMode.value = 'edit';
  editingId.value = p.id;
  form.name = p.name;
  form.base_url = p.base_url;
  // 编辑时密钥框留空（占位提示“留空=不修改”），不回填掩码
  form.api_key = '';
  form.model = p.model;
  form.temperature = p.temperature;
  form.max_tokens = p.max_tokens;
  form.timeout_sec = p.timeout_sec;
  form.enabled = p.enabled;
  showEditor.value = true;
}

async function saveProfile() {
  if (!form.name.trim() || !form.base_url.trim() || !form.model.trim()) {
    toast.err('名称、Base URL、模型标识不能为空');
    return;
  }
  if (editorMode.value === 'create' && !form.api_key.trim()) {
    toast.err('新增 profile 时 API Key 必填');
    return;
  }
  editorSaving.value = true;
  try {
    const payload = {
      name: form.name.trim(),
      base_url: form.base_url.trim(),
      model: form.model.trim(),
      temperature: Number(form.temperature),
      max_tokens: Number(form.max_tokens),
      timeout_sec: Number(form.timeout_sec),
      enabled: form.enabled,
    } as Record<string, any>;
    if (form.api_key.trim()) payload.api_key = form.api_key.trim();
    if (editorMode.value === 'create') {
      await http.post(`${BASE}/llm`, { profile: payload });
      toast.ok('模型配置已新增');
    } else {
      await http.put(`${BASE}/llm/${encodeURIComponent(editingId.value)}`, { profile: payload });
      toast.ok('模型配置已更新');
    }
    showEditor.value = false;
    await loadLlm();
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '保存失败');
  } finally {
    editorSaving.value = false;
  }
}

async function removeProfile(p: LlmProfile) {
  if (!(await confirmStore.confirm({
    title: '删除模型配置',
    message: `确定删除「${p.name}」？该操作不可恢复。`,
    danger: true,
    confirmText: '删除',
  }))) return;
  try {
    await http.delete(`${BASE}/llm/${encodeURIComponent(p.id)}`);
    toast.ok('模型配置已删除');
    await loadLlm();
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '删除失败');
  }
}

async function makeDefault(p: LlmProfile) {
  try {
    await http.post(`${BASE}/llm/${encodeURIComponent(p.id)}/default`, {});
    toast.ok(`已将「${p.name}」设为默认模型`);
    await loadLlm();
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '设置失败');
  }
}

// ── 交易所 ──────────────────────────────────────────────────────────────
const exchangeSaving = ref(false);
const exchange = reactive({
  testnet: false,
  wallet_address: '',
  master_address: '',
  private_key: '', // 掩码；编辑留空=不改
});

async function saveExchange() {
  exchangeSaving.value = true;
  try {
    await http.put(`${BASE}/exchange`, { config: { ...exchange } });
    toast.ok('交易所配置已保存');
    await loadAll();
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '交易所配置保存失败');
  } finally {
    exchangeSaving.value = false;
  }
}

// ── 飞书 ─────────────────────────────────────────────────────────────────
const feishuSaving = ref(false);
const feishu = reactive({
  base_url: '',
  webhook_url: '',
  webhook_secret: '',
  signal_webhook_url: '',
  signal_webhook_secret: '',
  non_trade_webhook_url: '',
  non_trade_webhook_secret: '',
  notify_categories: '',
});

async function saveFeishu() {
  feishuSaving.value = true;
  try {
    await http.put(`${BASE}/feishu`, { config: { ...feishu } });
    toast.ok('飞书配置已保存');
    await loadAll();
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '飞书配置保存失败');
  } finally {
    feishuSaving.value = false;
  }
}

// ── 加载 ─────────────────────────────────────────────────────────────────
async function loadLlm() {
  const { data } = await http.get(`${BASE}/llm`);
  profiles.value = Array.isArray(data.profiles) ? data.profiles : [];
  defaultProfileId.value = data.default_profile_id ?? null;
}

async function loadAll() {
  loading.value = true;
  try {
    const [, view] = await Promise.all([
      loadLlm(),
      http.get(BASE),
    ]);
    const v = view.data;
    Object.assign(exchange, {
      testnet: !!v?.exchange?.testnet,
      wallet_address: v?.exchange?.wallet_address ?? '',
      master_address: v?.exchange?.master_address ?? '',
      private_key: v?.exchange?.private_key ?? '',
    });
    Object.assign(feishu, {
      base_url: v?.feishu?.base_url ?? '',
      webhook_url: v?.feishu?.webhook_url ?? '',
      webhook_secret: v?.feishu?.webhook_secret ?? '',
      signal_webhook_url: v?.feishu?.signal_webhook_url ?? '',
      signal_webhook_secret: v?.feishu?.signal_webhook_secret ?? '',
      non_trade_webhook_url: v?.feishu?.non_trade_webhook_url ?? '',
      non_trade_webhook_secret: v?.feishu?.non_trade_webhook_secret ?? '',
      notify_categories: v?.feishu?.notify_categories ?? '',
    });
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '加载集成配置失败');
  } finally {
    loading.value = false;
  }
}

onMounted(loadAll);
</script>

<template>
  <div class="space-y-5">
    <header class="flex items-center justify-between flex-wrap gap-3">
      <div>
        <h2 class="text-xl font-semibold">集成配置</h2>
        <p class="text-sm text-[var(--text-muted)] mt-1">LLM 模型 · 交易所 API · 飞书，统一管理；密钥仅掩码展示</p>
      </div>
      <span v-if="!canWrite" class="text-xs px-2 py-1 rounded bg-amber-500/15 text-amber-300">
        当前账号为只读（缺少 config:write）
      </span>
    </header>

    <!-- Tab 切换（视觉区分） -->
    <div class="flex gap-2 flex-wrap">
      <button
        class="tab-btn" :class="{ active: activeTab === 'llm' }"
        @click="activeTab = 'llm'"
      >🧠 LLM 模型</button>
      <button
        class="tab-btn" :class="{ active: activeTab === 'exchange' }"
        @click="activeTab = 'exchange'"
      >🪙 交易所 API</button>
      <button
        class="tab-btn" :class="{ active: activeTab === 'feishu' }"
        @click="activeTab = 'feishu'"
      >🔔 飞书</button>
    </div>

    <div v-if="loading" class="card text-sm text-[var(--text-muted)] py-10 text-center">加载中…</div>

    <!-- ============ LLM ============ -->
    <div v-else-if="activeTab === 'llm'" class="space-y-4">
      <div class="flex items-center justify-between">
        <p class="text-sm text-[var(--text-muted)]">
          支持多个模型/Provider，默认模型将用于研判与对话；共 {{ profiles.length }} 个
        </p>
        <button v-if="canWrite" class="btn" @click="openCreate">＋ 新增模型</button>
      </div>

      <div v-if="profiles.length === 0" class="card text-sm text-[var(--text-muted)] py-10 text-center">
        暂无模型配置，点击「新增模型」创建
      </div>

      <div v-else class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div v-for="p in profiles" :key="p.id" class="card llm-card">
          <div class="flex items-start justify-between gap-2">
            <div>
              <div class="flex items-center gap-2">
                <span class="font-semibold">{{ p.name }}</span>
                <span v-if="p.id === defaultProfileId" class="tag tag-default">默认</span>
                <span v-if="!p.enabled" class="tag tag-off">停用</span>
              </div>
              <div class="text-xs text-[var(--text-muted)] mt-1 font-mono">{{ p.model }}</div>
            </div>
          </div>
          <dl class="mt-3 space-y-1 text-xs">
            <div class="fld"><dt>Base URL</dt><dd class="font-mono truncate">{{ p.base_url }}</dd></div>
            <div class="fld"><dt>API Key</dt><dd class="font-mono">{{ p.api_key || '—' }}</dd></div>
            <div class="fld">
              <dt>参数</dt>
              <dd>T={{ p.temperature }} · {{ p.max_tokens }} tokens · {{ p.timeout_sec }}s</dd>
            </div>
          </dl>
          <div v-if="canWrite" class="mt-3 pt-2 border-t border-[var(--border)] flex gap-2 flex-wrap">
            <button class="btn text-xs" @click="openEdit(p)">✏️ 编辑</button>
            <button
              v-if="p.id !== defaultProfileId" class="btn text-xs" @click="makeDefault(p)"
            >⭐ 设默认</button>
            <button class="btn text-xs btn-danger-text" @click="removeProfile(p)">🗑 删除</button>
          </div>
        </div>
      </div>
    </div>

    <!-- ============ 交易所 ============ -->
    <div v-else-if="activeTab === 'exchange'" class="card space-y-4 ex-card">
      <div class="flex items-center gap-2 pb-2 border-b border-[var(--border)]">
        <span class="font-semibold">Hyperliquid 交易所 API</span>
        <label class="ml-auto flex items-center gap-2 text-sm cursor-pointer">
          <input type="checkbox" :checked="exchange.testnet" :disabled="!canWrite"
            @change="(e) => (exchange.testnet = (e.target as HTMLInputElement).checked)" />
          测试网 Testnet
        </label>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label class="fld-label">资金账户地址（Master）</label>
          <input class="input w-full mt-1" v-model="exchange.master_address" :disabled="!canWrite" placeholder="0x…（统一账户模式）" />
        </div>
        <div>
          <label class="fld-label">签名钱包地址（Agent Wallet）</label>
          <input class="input w-full mt-1" v-model="exchange.wallet_address" :disabled="!canWrite" placeholder="0x…" />
        </div>
        <div class="md:col-span-2">
          <label class="fld-label">私钥（64 位十六进制）</label>
          <input
            class="input w-full mt-1 font-mono" type="password"
            :placeholder="exchange.private_key ? `已保存：${exchange.private_key}（留空不修改）` : '0x… / 无 0x 前缀'"
            v-model="exchange.private_key" :disabled="!canWrite"
          />
          <p class="text-[11px] text-[var(--text-muted)] mt-1">
            当前值：<span class="font-mono">{{ exchange.private_key || '未设置' }}</span>；清空输入框后保存即保留原私钥
          </p>
        </div>
      </div>

      <div v-if="canWrite" class="flex justify-end">
        <button class="btn btn-primary" :disabled="exchangeSaving" @click="saveExchange">
          {{ exchangeSaving ? '保存中…' : '保存交易所配置' }}
        </button>
      </div>
    </div>

    <!-- ============ 飞书 ============ -->
    <div v-else class="card space-y-4 feishu-card">
      <div class="flex items-center gap-2 pb-2 border-b border-[var(--border)]">
        <span class="font-semibold">飞书通知配置</span>
        <span class="text-xs text-[var(--text-muted)] ml-auto">自定义群机器人 Webhook</span>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div class="md:col-span-2">
          <label class="fld-label">系统访问地址 HERMES_BASE_URL（卡片按钮跳转）</label>
          <input class="input w-full mt-1" v-model="feishu.base_url" :disabled="!canWrite" placeholder="https://…" />
        </div>

        <div>
          <label class="fld-label">主 Webhook（交易执行）</label>
          <input class="input w-full mt-1" v-model="feishu.webhook_url" :disabled="!canWrite" placeholder="https://open.feishu.cn/…" />
        </div>
        <div>
          <label class="fld-label">主 Webhook 签名密钥</label>
          <input class="input w-full mt-1 font-mono" type="password"
            :placeholder="feishu.webhook_secret ? `已保存：${feishu.webhook_secret}（留空不改）` : ''"
            v-model="feishu.webhook_secret" :disabled="!canWrite" />
        </div>

        <div>
          <label class="fld-label">信号 Webhook（风控拦截/AI 结论）</label>
          <input class="input w-full mt-1" v-model="feishu.signal_webhook_url" :disabled="!canWrite" placeholder="留空回退主 Webhook" />
        </div>
        <div>
          <label class="fld-label">信号 Webhook 签名密钥</label>
          <input class="input w-full mt-1 font-mono" type="password"
            :placeholder="feishu.signal_webhook_secret ? `已保存：${feishu.signal_webhook_secret}（留空不改）` : ''"
            v-model="feishu.signal_webhook_secret" :disabled="!canWrite" />
        </div>

        <div>
          <label class="fld-label">非交易 Webhook（系统/报表）</label>
          <input class="input w-full mt-1" v-model="feishu.non_trade_webhook_url" :disabled="!canWrite" placeholder="留空回退主 Webhook" />
        </div>
        <div>
          <label class="fld-label">非交易 Webhook 签名密钥</label>
          <input class="input w-full mt-1 font-mono" type="password"
            :placeholder="feishu.non_trade_webhook_secret ? `已保存：${feishu.non_trade_webhook_secret}（留空不改）` : ''"
            v-model="feishu.non_trade_webhook_secret" :disabled="!canWrite" />
        </div>

        <div class="md:col-span-2">
          <label class="fld-label">通知类别白名单（逗号分隔）</label>
          <input class="input w-full mt-1" v-model="feishu.notify_categories" :disabled="!canWrite"
            placeholder="trade,signal,risk,system,ai,surge,report" />
        </div>
      </div>

      <div v-if="canWrite" class="flex justify-end">
        <button class="btn btn-primary" :disabled="feishuSaving" @click="saveFeishu">
          {{ feishuSaving ? '保存中…' : '保存飞书配置' }}
        </button>
      </div>
    </div>

    <!-- LLM 新增/编辑弹窗 -->
    <Teleport to="body">
      <div v-if="showEditor" class="modal-mask" @click.self="showEditor = false">
        <div class="modal-box">
          <h3 class="font-semibold mb-4">{{ editorMode === 'create' ? '新增模型配置' : '编辑模型配置' }}</h3>
          <div class="space-y-3">
            <div>
              <label class="fld-label">显示名称</label>
              <input class="input w-full mt-1" v-model="form.name" placeholder="如 OpenRouter 主力" />
            </div>
            <div>
              <label class="fld-label">Base URL</label>
              <input class="input w-full mt-1 font-mono" v-model="form.base_url" placeholder="https://openrouter.ai/api/v1" />
            </div>
            <div>
              <label class="fld-label">模型标识 model</label>
              <input class="input w-full mt-1 font-mono" v-model="form.model" placeholder="deepseek-chat" />
            </div>
            <div>
              <label class="fld-label">
                API Key
                <span v-if="editorMode === 'edit'" class="text-[var(--text-muted)] font-normal">（留空=不修改）</span>
              </label>
              <input class="input w-full mt-1 font-mono" type="password" v-model="form.api_key"
                :placeholder="editorMode === 'create' ? 'sk-…' : '已保存，留空不修改'" />
            </div>
            <div class="grid grid-cols-3 gap-3">
              <div>
                <label class="fld-label">temperature</label>
                <input type="number" step="0.1" class="input w-full mt-1" v-model="form.temperature" />
              </div>
              <div>
                <label class="fld-label">max_tokens</label>
                <input type="number" class="input w-full mt-1" v-model="form.max_tokens" />
              </div>
              <div>
                <label class="fld-label">timeout(s)</label>
                <input type="number" step="0.5" class="input w-full mt-1" v-model="form.timeout_sec" />
              </div>
            </div>
            <label class="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" v-model="form.enabled" /> 启用该配置
            </label>
          </div>
          <div class="flex justify-end gap-2 mt-5">
            <button class="btn" @click="showEditor = false">取消</button>
            <button class="btn btn-primary" :disabled="editorSaving" @click="saveProfile">
              {{ editorSaving ? '保存中…' : '确定' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.tab-btn {
  padding: 6px 14px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--surface);
  font-size: 13px;
  color: var(--text-2);
  transition: all 0.15s;
}
.tab-btn:hover { border-color: var(--primary); }
.tab-btn.active {
  background: var(--primary);
  border-color: var(--primary);
  color: #fff;
}

.fld-label { font-size: 12px; color: var(--text-2); display: block; }

.fld { display: flex; gap: 8; }
.fld dt { width: 64px; color: var(--text-muted); flex-shrink: 0; }
.fld dd { margin: 0; color: var(--text-2); min-width: 0; }

.tag { font-size: 10px; padding: 1px 6px; border-radius: 4px; }
.tag-default { background: #16a34a22; color: #4ade80; }
.tag-off { background: #6b72802b; color: #9ca3af; }

.btn-danger-text { color: #f87171; }

/* 三区差异化描边 */
.llm-card { border-left: 3px solid #6366f1; }
.ex-card { border-left: 3px solid #d97706; }
.feishu-card { border-left: 3px solid #0ea5e9; }

/* 弹窗 */
.modal-mask {
  position: fixed; inset: 0; background: rgba(0,0,0,0.5);
  display: flex; align-items: center; justify-content: center; z-index: 1000;
}
.modal-box {
  width: min(560px, 92vw); max-height: 90vh; overflow-y: auto;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 20px;
}
.btn-primary { background: var(--primary); color: #fff; border-color: var(--primary); }
</style>
