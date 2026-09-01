<script setup lang="ts">
/**
 * 全局提醒设置模块
 * - 弹窗位置：右下角 / 屏幕中央 / 右上角 / 左下角
 * - 语音播报：开关 + 音量 + 可选自定义提醒音
 * - 事件类型勾选：按 交易/风险/智能体/系统 分类
 * - 保存即时生效（写入 AlertConfig 单行记录）
 */
import { computed, onMounted, reactive, ref } from 'vue';
import http from '@/shared/api/client';
import { useToast } from '@/stores/toast';
import { useAlertStore } from '@/stores/alerts';
import { useVoiceBroadcast } from '@/shared/composables/useVoiceBroadcast';
import type { AlertConfig, AlertEvent, CustomVoice } from '@/shared/types';

const toast = useToast();
const alertStore = useAlertStore();
const { broadcast, stop } = useVoiceBroadcast();

// 默认配置（首屏渲染前使用，加载完成后被服务端数据覆盖）
const DEFAULT_CONFIG: AlertConfig = {
  popup_position: 'bottom-right',
  voice_enabled: false,
  voice_id: null,
  voice_volume: 0.8,
  event_types: [],
  event_voices: {},
};

const config = ref<AlertConfig>({ ...DEFAULT_CONFIG });
const events = ref<AlertEvent[]>([]);
const voices = ref<CustomVoice[]>([]);
const loading = ref(true);
const saving = ref(false);
const uploading = ref(false);

// 上传表单
const uploadForm = reactive({ name: '', file: null as File | null });
// 自定义提醒音折叠面板
const showCustomVoices = ref(false);

// 已上传的自定义提醒音（id 一定存在）
type SavedVoice = CustomVoice & { id: string };
const customVoices = computed<SavedVoice[]>(() =>
  voices.value.filter((v): v is SavedVoice => !!v.id),
);

// 按分类聚合事件
const grouped = computed(() => {
  const map: Record<string, AlertEvent[]> = {};
  for (const e of events.value) {
    (map[e.category] ||= []).push(e);
  }
  return map;
});
const CATEGORY_LABEL: Record<string, string> = {
  trade: '交易事件',
  risk: '风险事件',
  agent: '智能体事件',
  system: '系统事件',
};

const positions = [
  { value: 'bottom-right', label: '右下角', icon: '↘️' },
  { value: 'bottom-left', label: '左下角', icon: '↙️' },
  { value: 'top-right', label: '右上角', icon: '↗️' },
  { value: 'center', label: '屏幕中央', icon: '🎯' },
];

async function load() {
  loading.value = true;
  try {
    const [c, e, v] = await Promise.all([
      http.get<AlertConfig>('/api/portal/alerts/config'),
      http.get<AlertEvent[]>('/api/portal/alerts/events'),
      http.get<CustomVoice[]>('/api/portal/alerts/voices'),
    ]);
    config.value = c.data;
    events.value = e.data;
    voices.value = v.data;
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '加载提醒设置失败');
  } finally {
    loading.value = false;
  }
}

function toggleEvent(code: string) {
  if (!config.value) return;
  const set = new Set(config.value.event_types);
  if (set.has(code)) set.delete(code);
  else set.add(code);
  config.value.event_types = [...set];
}

function selectAll(category: string, checked: boolean) {
  if (!config.value) return;
  const codes = events.value.filter((e) => e.category === category).map((e) => e.code);
  const set = new Set(config.value.event_types);
  codes.forEach((c) => (checked ? set.add(c) : set.delete(c)));
  config.value.event_types = [...set];
}

/**
 * 事件独立声音选择（select 选项 value）：
 *   ''           → 跟随全局（event_voices 中无此 key）
 *   '__mute__'   → 该事件静默（event_voices[code] = null）
 *   '<voice id>' → 使用指定自定义语音
 */
function eventVoiceValue(code: string): string {
  const v = config.value.event_voices?.[code];
  if (v === undefined) return '';
  if (v === null) return '__mute__';
  return v;
}
function setEventVoice(code: string, raw: string) {
  if (!config.value.event_voices) config.value.event_voices = {};
  const map = { ...config.value.event_voices };
  if (raw === '') delete map[code];
  else if (raw === '__mute__') map[code] = null;
  else map[code] = raw;
  config.value.event_voices = map;
}
function eventVoiceLabel(code: string): string {
  const v = config.value.event_voices?.[code];
  if (v === undefined) return '跟随全局';
  if (v === null) return '静默';
  const found = customVoices.value.find((x) => x.id === v);
  return found ? found.name : '跟随全局';
}

async function save() {
  if (!config.value) return;
  saving.value = true;
  try {
    const { data } = await http.put<AlertConfig>('/api/portal/alerts/config', config.value);
    config.value = data;
    // 同步到全局 store，让正在运行的会话即时生效
    alertStore.config = data;
    toast.ok('提醒设置已保存并即时生效');
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '保存失败');
  } finally {
    saving.value = false;
  }
}

function onFileChange(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0];
  if (f) uploadForm.file = f;
}

async function uploadVoice() {
  if (!uploadForm.file || !uploadForm.name.trim()) {
    toast.err('请填写名称并选择文件');
    return;
  }
  if (uploadForm.file.size > 2 * 1024 * 1024) {
    toast.err('文件不能超过 2MB');
    return;
  }
  uploading.value = true;
  try {
    const fd = new FormData();
    fd.append('name', uploadForm.name.trim());
    fd.append('file', uploadForm.file);
    await http.post('/api/portal/alerts/voices', fd, { headers: { 'Content-Type': 'multipart/form-data' } });
    toast.ok('提醒音上传成功');
    uploadForm.name = '';
    uploadForm.file = null;
    await load();
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '上传失败');
  } finally {
    uploading.value = false;
  }
}

async function deleteVoice(v: CustomVoice) {
  if (!v.id) return;
  if (!confirm(`确定删除提醒音「${v.name}」？`)) return;
  try {
    await http.delete(`/api/portal/alerts/voices/${v.id}`);
    toast.ok('已删除');
    // 如果删的是当前选中的，重置为默认
    if (config.value.voice_id === v.id) config.value.voice_id = null;
    await load();
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '删除失败');
  }
}

/** 试听：根据 voiceId 选择自定义音频或系统朗读 */
function preview(v: CustomVoice) {
  stop();
  if (v.id) {
    broadcast('试听自定义提醒音', { voiceId: v.id, volume: config.value?.voice_volume ?? 0.8, enabled: true });
  } else {
    broadcast('这是系统默认语音试听。', { voiceId: null, volume: config.value?.voice_volume ?? 0.8, enabled: true });
  }
}

const sampleEvents: Array<{
  type: string;
  title: string;
  content: string;
  level: 'info' | 'success' | 'warn' | 'danger';
  label: string;
}> = [
  {
    type: 'order_filled',
    title: '开仓成交 — TESTBTC',
    content: '方向 做多，金额 $1000.00，入场价 50000.0',
    level: 'success',
    label: '✅ 成交提醒',
  },
  {
    type: 'agent_signal',
    title: '智能体交易信号',
    content: 'BTCUSDT 4小时突破阻力位，建议做多，信号强度 8.5',
    level: 'success',
    label: '🟢 交易信号',
  },
  {
    type: 'position_opened',
    title: '持仓已开仓',
    content: 'ETHUSDT 多单 0.5 张，开仓价 2450.30',
    level: 'info',
    label: '🔵 开仓提醒',
  },
  {
    type: 'stop_loss_triggered',
    title: '止损触发',
    content: 'SOLUSDT 空单触发止损，亏损 -1.8%',
    level: 'danger',
    label: '🔴 止损告警',
  },
  {
    type: 'risk_alert',
    title: '风险预警',
    content: '账户总仓位占比 82%，接近上限 85%',
    level: 'warn',
    label: '🟡 风险预警',
  },
  {
    type: 'system_error',
    title: '系统异常',
    content: '交易所 WebSocket 断连，正在自动重连（第 2 次）',
    level: 'danger',
    label: '⚫ 系统异常',
  },
];

/** 模拟一次事件：先同步 alertStore.config，再 emit */
function simulate(ev: (typeof sampleEvents)[number]) {
  alertStore.config = { ...config.value };
  alertStore.emit({
    type: ev.type,
    title: ev.title,
    content: ev.content,
    level: ev.level,
  });
  toast.ok(`已触发：${ev.title}`);
}

onMounted(load);
</script>

<template>
  <div class="space-y-5">
    <header>
      <h2 class="text-xl font-semibold">弹窗与语音提醒</h2>
      <p class="text-sm text-[var(--text-muted)] mt-1">
        设置提醒弹窗的位置、是否语音播报，以及需要接收哪些事件。保存后即时生效。
      </p>
    </header>

    <div v-if="loading" class="card text-center py-10 text-[var(--text-muted)]">加载中...</div>

    <template v-else>
      <!-- 测试触发：验证弹窗位置 + 语音播报 -->
      <section
        class="card space-y-3 border-2 border-dashed border-[var(--accent)]/40"
      >
        <div class="flex items-center justify-between flex-wrap gap-2">
          <div>
            <h3 class="font-semibold">🧪 测试提醒</h3>
            <p class="text-xs text-[var(--text-muted)] mt-1">
              点击按钮在当前浏览器触发一条测试事件，验证弹窗和语音是否正常。打开浏览器控制台（F12）可查看详细日志。
            </p>
          </div>
          <div class="flex flex-wrap gap-2">
            <button
              v-for="ev in sampleEvents"
              :key="ev.type"
              class="btn text-xs"
              :class="{
                'btn-danger': ev.level === 'danger',
                'btn-primary': ev.level === 'success',
              }"
              @click="simulate(ev)"
            >
              {{ ev.label }}
            </button>
          </div>
        </div>
      </section>

      <!-- 弹窗位置 -->
      <section class="card space-y-3">
        <h3 class="font-semibold">弹窗位置</h3>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
          <button
            v-for="p in positions"
            :key="p.value"
            class="border rounded-lg p-4 text-center transition"
            :class="config.popup_position === p.value
              ? 'border-[var(--accent)] bg-[var(--accent)]/10'
              : 'border-[var(--border)] hover:border-[var(--accent)]/50'"
            @click="config.popup_position = p.value as any"
          >
            <div class="text-2xl">{{ p.icon }}</div>
            <div class="text-sm mt-1">{{ p.label }}</div>
          </button>
        </div>
      </section>

      <!-- 语音播报（精简版） -->
      <section class="card space-y-4">
        <h3 class="font-semibold">语音播报</h3>

        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" v-model="config.voice_enabled" />
          <span>提醒弹窗出现时，同步用语音读出内容</span>
        </label>

        <!-- 未开启时给一句简短说明 -->
        <p v-if="!config.voice_enabled" class="text-xs text-[var(--text-muted)] -mt-2">
          关闭后仅显示弹窗，不发出声音。
        </p>

        <div v-show="config.voice_enabled" class="space-y-4">
          <!-- 音量 -->
          <div>
            <label class="label block">音量：{{ Math.round(config.voice_volume * 100) }}%</label>
            <input type="range" min="0" max="1" step="0.05" class="w-full" v-model.number="config.voice_volume" />
          </div>

          <!-- 用什么声音：默认浏览器朗读 / 自定义提醒音 -->
          <div>
            <label class="label block">提醒声音</label>
            <select class="input w-full" v-model="config.voice_id">
              <option :value="null">浏览器语音朗读（默认，无需配置）</option>
              <option v-for="v in customVoices" :key="v.id" :value="v.id">
                {{ v.name }}
              </option>
            </select>
            <p class="text-xs text-[var(--text-muted)] mt-1">
              默认使用浏览器自带的语音朗读。如需自定义提示音，可在下方展开上传。
            </p>
            <button class="btn text-xs mt-2" @click="preview({ id: config.voice_id, name: '当前选择' } as CustomVoice)">
              ▶ 试听当前声音
            </button>
          </div>

          <!-- 自定义提醒音（折叠，可选） -->
          <div class="border-t border-[var(--border)] pt-3">
            <button
              type="button"
              class="flex items-center gap-1 text-sm text-[var(--text-muted)] hover:text-[var(--text)] transition"
              @click="showCustomVoices = !showCustomVoices"
            >
              <span>{{ showCustomVoices ? '▼' : '▶' }}</span>
              自定义提醒音管理（可选）
            </button>

            <div v-if="showCustomVoices" class="mt-3 space-y-3">
              <p class="text-xs text-[var(--text-muted)]">
                上传 mp3 / wav / ogg 音频（单个不超过 2MB），上传后可在上方"提醒声音"中选用。
              </p>

              <!-- 已有列表 -->
              <div
                v-for="v in customVoices"
                :key="v.id"
                class="flex items-center justify-between text-sm border border-[var(--border)] rounded px-3 py-2"
              >
                <span class="font-medium">{{ v.name }}</span>
                <div class="flex gap-2">
                  <button class="btn text-xs" @click="preview(v)">▶ 试听</button>
                  <button class="btn btn-danger text-xs" @click="deleteVoice(v)">删除</button>
                </div>
              </div>
              <p v-if="customVoices.length === 0" class="text-xs text-[var(--text-muted)]">
                还没有自定义提醒音。
              </p>

              <!-- 上传 -->
              <div class="flex flex-wrap gap-2 items-end p-3 border border-dashed border-[var(--border)] rounded">
                <div class="flex-1 min-w-[160px]">
                  <label class="label block">名称</label>
                  <input class="input w-full" v-model="uploadForm.name" placeholder="例如：风控警报音" />
                </div>
                <div class="flex-1 min-w-[200px]">
                  <label class="label block">音频文件（mp3/wav/ogg）</label>
                  <input type="file" accept="audio/mpeg,audio/wav,audio/ogg" class="text-sm" @change="onFileChange" />
                </div>
                <button class="btn btn-primary" :disabled="uploading" @click="uploadVoice">
                  {{ uploading ? '上传中...' : '上传' }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 事件类型勾选 -->
      <section class="card space-y-4">
        <h3 class="font-semibold">需要提醒的事件</h3>
        <div v-for="(items, cat) in grouped" :key="cat" class="border border-[var(--border)] rounded p-3">
          <div class="flex items-center justify-between mb-2">
            <h4 class="text-sm font-semibold">{{ CATEGORY_LABEL[cat] || cat }}</h4>
            <div class="flex gap-3 text-xs text-[var(--text-muted)]">
              <label class="cursor-pointer">
                <input
                  type="checkbox"
                  :checked="items.every((i) => config.event_types.includes(i.code))"
                  @change="(e) => selectAll(cat, (e.target as HTMLInputElement).checked)"
                />
                全选
              </label>
            </div>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
            <div
              v-for="ev in items"
              :key="ev.code"
              class="flex items-center gap-2 text-sm border border-[var(--border)] rounded px-2 py-1.5"
            >
              <label class="flex items-center gap-2 cursor-pointer flex-1 min-w-0">
                <input type="checkbox" :checked="config.event_types.includes(ev.code)" @change="toggleEvent(ev.code)" />
                <span class="truncate">{{ ev.name }}</span>
              </label>
              <select
                v-if="ev.voice && config.voice_enabled"
                class="text-xs py-1 px-1.5 max-w-[140px] bg-[var(--bg)] border border-[var(--border)] rounded"
                :value="eventVoiceValue(ev.code)"
                :title="`该事件声音：${eventVoiceLabel(ev.code)}`"
                @change="(e) => setEventVoice(ev.code, (e.target as HTMLSelectElement).value)"
              >
                <option value="">跟随全局</option>
                <option value="__mute__">静默</option>
                <option v-for="v in customVoices" :key="v.id" :value="v.id">{{ v.name }}</option>
              </select>
            </div>
          </div>
        </div>
      </section>

      <div class="sticky bottom-4 flex justify-end">
        <button class="btn btn-primary px-8 shadow-lg" :disabled="saving" @click="save">
          {{ saving ? '保存中...' : '保存设置' }}
        </button>
      </div>
    </template>
  </div>
</template>
