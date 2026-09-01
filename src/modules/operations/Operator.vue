<script setup lang="ts">
/**
 * 操作员控制台
 * - LIVE / SHADOW / OFF 模式切换（SHADOW 跑完整链路但不真实下单）
 * - 追踪持仓（DSL 移动止盈追踪器）列表与强平
 * - 命令行终端（status/pause/resume/shadow/close <coin>/regime/config/help）
 * - 数据与指令均经 BFF 代理到 hermes-trader 的 /api/dashboard/operator/*
 */
import { nextTick, onMounted, onUnmounted, ref } from 'vue';
import http from '@/shared/api/client';
import { useToast } from '@/stores/toast';
import { usePortalStore } from '@/stores/portal';

const toast = useToast();
const portal = usePortalStore();

const cfg = ref<any>({});
const trackers = ref<any[]>([]);
const loading = ref(true);
const busy = ref(false);
const command = ref('');
const terminal = ref<{ cmd: string; resp: string; kind: string }[]>([]);
const termEl = ref<HTMLElement | null>(null);
let timer: number | null = null;

async function load() {
  try {
    const [c, t] = await Promise.all([
      http.get('/api/portal/trader/api/dashboard/operator/config'),
      http.get('/api/portal/trader/api/dashboard/operator/trackers'),
    ]);
    cfg.value = c.data;
    trackers.value = t.data;
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '加载操作员数据失败');
  } finally {
    loading.value = false;
  }
}

type Mode = 'LIVE' | 'SHADOW' | 'OFF';

async function setMode(mode: Mode) {
  busy.value = true;
  try {
    await http.post('/api/portal/trader/api/dashboard/operator/mode', { mode });
    cfg.value.mode = mode;
    await portal.loadMode();
    const label = mode === 'SHADOW' ? 'SHADOW（影子盘，不真实下单）' : mode;
    toast.ok(`模式已切换为 ${label}`);
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '切换失败');
  } finally {
    busy.value = false;
  }
}

async function forceClose(coin: string) {
  if (!confirm(`确定强制平仓 ${coin}？`)) return;
  try {
    await http.post('/api/portal/trader/api/dashboard/operator/close', { coin });
    toast.ok(`${coin} 已市价平仓`);
    await load();
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '平仓失败');
  }
}

async function runCommand() {
  const cmd = command.value.trim();
  if (!cmd) return;
  terminal.value.push({ cmd, resp: '', kind: 'pending' });
  command.value = '';
  await nextTick();
  scrollTerm();
  try {
    const { data } = await http.post('/api/portal/trader/api/dashboard/operator/terminal', { command: cmd });
    const entry = terminal.value[terminal.value.length - 1];
    entry.resp = data.response || '(无响应)';
    entry.kind = data.kind || 'ok';
  } catch (e: any) {
    const entry = terminal.value[terminal.value.length - 1];
    entry.resp = e?.response?.data?.detail || '命令执行失败';
    entry.kind = 'error';
  } finally {
    scrollTerm();
  }
}

function scrollTerm() {
  if (termEl.value) termEl.value.scrollTop = termEl.value.scrollHeight;
}

const COMMANDS = [
  { cmd: 'status', desc: '查看当前运行状态' },
  { cmd: 'pause', desc: '暂停交易循环' },
  { cmd: 'resume', desc: '恢复交易循环' },
  { cmd: 'shadow', desc: '切到影子盘：跑完整链路但不真实下单' },
  { cmd: 'close <币种>', desc: '市价平仓指定币种，如 close BTC' },
  { cmd: 'regime', desc: '查看当前市场状态' },
  { cmd: 'config', desc: '查看当前配置' },
  { cmd: 'help', desc: '显示命令帮助' },
];

function modeClass(mode?: string) {
  if (mode === 'LIVE') return 'text-emerald-400';
  if (mode === 'SHADOW') return 'text-amber-400';
  if (mode === 'OFF') return 'text-slate-400';
  return 'text-slate-400';
}

function sideLabel(s: string) {
  return s === 'long' ? '做多' : s === 'short' ? '做空' : s;
}

function fmt(v: any, d = 4) {
  if (v === null || v === undefined) return '—';
  return Number(v).toLocaleString('en-US', { maximumFractionDigits: d });
}

onMounted(() => {
  load();
  timer = window.setInterval(load, 10000);
});
onUnmounted(() => { if (timer) window.clearInterval(timer); });
</script>

<template>
  <div class="space-y-5">
    <header>
      <h2 class="text-xl font-semibold">操作员控制台</h2>
      <p class="text-sm text-[var(--text-muted)] mt-1">LIVE/SHADOW/OFF 模式切换、追踪止盈监控、命令终端</p>
    </header>

    <div v-if="loading" class="card text-center py-10 text-[var(--text-muted)]">加载中...</div>

    <template v-else>
      <!-- 模式切换 -->
      <div class="card flex items-center justify-between flex-wrap gap-3">
        <div>
          <div class="text-xs text-[var(--text-muted)]">当前运行模式</div>
          <div class="text-2xl font-bold mt-1" :class="modeClass(cfg.mode)">
            {{ cfg.mode || '—' }}
            <span v-if="cfg.mode === 'SHADOW'" class="text-sm font-normal align-middle">影子盘（不真实下单）</span>
          </div>
        </div>
        <div class="flex gap-2">
          <button class="btn btn-primary" :disabled="busy || cfg.mode === 'LIVE'" @click="setMode('LIVE')">▶️ 切到 LIVE</button>
          <button
            class="btn"
            style="border-color: var(--warn); color: var(--warn)"
            :disabled="busy || cfg.mode === 'SHADOW'"
            @click="setMode('SHADOW')"
          >◐ 切到 SHADOW（模拟盘）</button>
          <button class="btn btn-danger" :disabled="busy || cfg.mode === 'OFF'" @click="setMode('OFF')">⏸️ 切到 OFF</button>
        </div>
      </div>

      <!-- 追踪器 -->
      <div class="card">
        <h3 class="font-semibold mb-3">移动止盈追踪器</h3>
        <div v-if="trackers.length === 0" class="text-sm text-[var(--text-muted)] py-6 text-center">暂无活跃追踪器</div>
        <table v-else class="w-full text-sm">
          <thead>
            <tr class="text-left text-[var(--text-muted)] border-b border-[var(--border)]">
              <th class="py-2 px-3">币种</th>
              <th class="py-2 px-3">方向</th>
              <th class="py-2 px-3 text-right">入场价</th>
              <th class="py-2 px-3 text-right">峰值/谷值</th>
              <th class="py-2 px-3 text-right">止损地板</th>
              <th class="py-2 px-3 text-right">连续击穿</th>
              <th class="py-2 px-3 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in trackers" :key="t.key" class="border-b border-[var(--border)] last:border-0">
              <td class="py-2 px-3 font-mono">{{ t.coin }}</td>
              <td class="py-2 px-3">
                <span :class="t.side === 'long' ? 'badge-ok' : 'badge-danger'">{{ sideLabel(t.side) }}</span>
              </td>
              <td class="py-2 px-3 text-right font-mono">{{ fmt(t.entry_px, 2) }}</td>
              <td class="py-2 px-3 text-right font-mono">{{ fmt(t.peak_px, 2) }}</td>
              <td class="py-2 px-3 text-right font-mono text-amber-400">{{ fmt(t.floor_px, 2) }}</td>
              <td class="py-2 px-3 text-right">{{ t.consecutive_breaches }}</td>
              <td class="py-2 px-3 text-right">
                <button class="btn btn-danger text-xs" @click="forceClose(t.coin)">市价平仓</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 命令终端 -->
      <div class="card">
        <h3 class="font-semibold mb-3">命令终端</h3>
        <div class="flex flex-wrap gap-1.5 mb-2">
          <button
            v-for="c in COMMANDS"
            :key="c.cmd"
            class="text-[11px] px-2 py-0.5 rounded bg-white/5 hover:bg-white/10 text-[var(--text-muted)] hover:text-white transition-colors"
            :title="c.desc"
            @click="command = c.cmd.startsWith('close') ? 'close BTC' : c.cmd"
          >
            <span class="font-mono">{{ c.cmd }}</span>
            <span class="ml-1 opacity-60">{{ c.desc }}</span>
          </button>
        </div>
        <div ref="termEl" class="bg-black/60 rounded p-3 h-72 overflow-y-auto font-mono text-xs space-y-1">
          <div v-for="(line, i) in terminal" :key="i">
            <div class="text-emerald-400">$ {{ line.cmd }}</div>
            <div class="text-slate-300 whitespace-pre-wrap pl-2">{{ line.resp || '执行中...' }}</div>
          </div>
        </div>
        <form class="flex gap-2 mt-3" @submit.prevent="runCommand">
          <input
            class="input flex-1 font-mono"
            v-model="command"
            placeholder="输入命令：status / pause / resume / shadow / close BTC / regime / config / help"
          />
          <button class="btn btn-primary" type="submit">执行</button>
        </form>
      </div>
    </template>
  </div>
</template>
