<script setup lang="ts">
/**
 * 影子臂评级中心（M3，Audit 2026-09-07）
 * - 数据来源：hermes-trader /api/dashboard/shadow-arms/*（经 BFF 代理，operator:mode）
 * - 展示：12 条风控臂的夜间评级（mode/verdict/三窗命中统计/成熟度）、真实成交基线、
 *   每晚评级历史快照
 * - INERT 红线：本页只读。评级器只评级 + 飞书建议，绝不自动改配置/闸门/下单；
 *   PROMOTE_CANDIDATE 仅为建议，「去升级」只跳转 /config 由人工走 config_store 权威写。
 *   页内不存在任何改 mode / 下单控件。
 * - 刷新：30s 轮询 + SSE（risk_gate_blind / shadow_arms_refresh）防抖触发；
 *   operator 可点「立即重评」调 POST refresh（手动重算，不写历史快照）。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import http from '@/shared/api/client';
import { useAuthStore } from '@/stores/auth';
import { useToast } from '@/stores/toast';
import { useSseFeedStore } from '@/stores/sseFeed';

const API = '/api/portal/trader/api/dashboard/shadow-arms';
const WINDOWS = '24,72,168';

const router = useRouter();
const auth = useAuthStore();
const toast = useToast();

const loading = ref(true);
const error = ref('');
const refreshing = ref(false);
const report = ref<any>(null);
const history = ref<any[]>([]);

let timer: number | null = null;
let sseUnsub: (() => void) | null = null;
let pendingRefresh: number | null = null;

const canRefresh = computed(() => auth.hasPermission('operator:mode'));

const REFRESH_EVENTS = new Set(['risk_gate_blind', 'shadow_arms_refresh']);
function scheduleRefresh() {
  if (pendingRefresh !== null) return;
  pendingRefresh = window.setTimeout(() => {
    pendingRefresh = null;
    loadAll(false);
  }, 500);
}

// ---------------------------------------------------------------- data
async function loadAll(showLoading = true) {
  try {
    error.value = '';
    if (showLoading) loading.value = true;
    const [g, h] = await Promise.all([
      http.get(`${API}/grades`, { params: { windows: WINDOWS } }),
      http.get(`${API}/grade-history`, { params: { days: 30, limit: 400 } }),
    ]);
    report.value = g.data;
    history.value = Array.isArray(h.data?.snapshots) ? h.data.snapshots : [];
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '加载影子臂评级失败';
  } finally {
    loading.value = false;
  }
}

async function refreshNow() {
  if (!canRefresh.value || refreshing.value) return;
  refreshing.value = true;
  try {
    const { data } = await http.post(`${API}/refresh`, { windows: WINDOWS });
    const { ok, ...payload } = data || {};
    report.value = payload;
    toast.ok('已立即重评（只读建议，未改动任何闸门/配置）');
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '立即重评失败');
  } finally {
    refreshing.value = false;
  }
}

// ---------------------------------------------------------------- derived
const arms = computed<any[]>(() => report.value?.arms || []);
const baseline = computed<any>(() => report.value?.real_baseline || {});
const windowsH = computed<number[]>(() => report.value?.windows_h || [24, 72, 168]);

const gapArms = computed(() => arms.value.filter((a) => a.verdict === 'DATA_GAP'));
const reviewArms = computed(() => arms.value.filter((a) => a.verdict === 'REVIEW'));
const promoteArms = computed(() => arms.value.filter((a) => a.verdict === 'PROMOTE_CANDIDATE'));
const collectingArms = computed(() =>
  arms.value.filter((a) => a.verdict === 'INSUFFICIENT_DATA' || a.verdict === 'COLLECTING'),
);
const offArms = computed(() => arms.value.filter((a) => a.verdict === 'OFF'));

const recentHistory = computed(() => history.value.slice(-10).reverse());

// 成熟度：最长窗口样本数 vs MIN_SAMPLES_PROMOTE=60（与后端 scripts/shadow_grade.py 对齐）
const MIN_SAMPLES = 60;
function longestWindow(a: any) {
  const wins: any[] = Array.isArray(a?.windows) ? a.windows : [];
  const wLong = Math.max(...windowsH.value);
  return wins.find((s) => s.window_h === wLong) || wins[wins.length - 1] || {};
}
function maturityPct(a: any): number {
  const total = Number(longestWindow(a)?.total ?? 0);
  return Math.max(0, Math.min(100, (total / MIN_SAMPLES) * 100));
}

// ---------------------------------------------------------------- formatters
const VERDICT_BADGE: Record<string, string> = {
  DATA_GAP: 'badge-danger',
  REVIEW: 'badge-warn',
  PROMOTE_CANDIDATE: 'badge-ok',
  INSUFFICIENT_DATA: 'badge-muted',
  COLLECTING: 'badge-muted',
  OFF: 'badge-muted',
};
function verdictBadge(v: string) {
  return VERDICT_BADGE[v] || 'badge-muted';
}
function modeBadge(mode: string) {
  if (mode === 'enforce') return 'badge-ok';
  if (mode === 'shadow') return 'badge-purple';
  return 'badge-muted';
}
const KIND_CN: Record<string, string> = { block: '拦截', change: '调整' };
function kindCN(k: string) {
  return KIND_CN[k] || k || '—';
}
function hitRateTxt(s: any) {
  const d = Number(s?.decisions ?? 0);
  if (!d) return '—';
  return `${(Number(s?.hit_rate ?? 0) * 100).toFixed(1)}%`;
}
function winRateTxt(wr: any) {
  if (wr === null || wr === undefined || isNaN(Number(wr))) return '—';
  return (Number(wr) * 100).toFixed(1) + '%';
}
function fmtDay(ts: any) {
  const t = typeof ts === 'number' ? (ts < 1e12 ? ts * 1000 : ts) : Date.now();
  return new Date(t).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}
function snapshotCounts(snap: any) {
  const c: Record<string, number> = {};
  for (const a of snap?.arms || []) c[a.verdict] = (c[a.verdict] || 0) + 1;
  return c;
}

onMounted(() => {
  loadAll();
  const sse = useSseFeedStore();
  sseUnsub = sse.onEvent((data) => {
    if (data?.event && REFRESH_EVENTS.has(data.event)) scheduleRefresh();
  });
  timer = window.setInterval(() => loadAll(false), 30000);
});
onUnmounted(() => {
  if (timer) window.clearInterval(timer);
  if (pendingRefresh !== null) window.clearTimeout(pendingRefresh);
  sseUnsub?.();
  sseUnsub = null;
});
</script>

<template>
  <div class="space-y-5">
    <header class="flex items-center justify-between flex-wrap gap-3">
      <div>
        <h2 class="text-xl font-semibold flex items-center gap-2">
          影子臂评级中心
          <span class="badge badge-purple">SHADOW 评级</span>
          <span class="badge badge-muted">只读 · 建议</span>
        </h2>
        <p class="text-sm text-[var(--text-muted)] mt-1">
          夜间评级器对 12 条风控臂的采数/命中/回填反事实评级 · 30s 自动刷新
          <span v-if="report?.generated_at" class="ml-2">评级生成于 {{ report.generated_at }}</span>
        </p>
      </div>
      <div class="flex gap-2 items-center flex-wrap">
        <button class="btn" @click="loadAll()">🔄 刷新</button>
        <button v-if="canRefresh" class="btn btn-primary text-xs" :disabled="refreshing" @click="refreshNow">
          {{ refreshing ? '重评中...' : '⚡ 立即重评' }}
        </button>
      </div>
    </header>

    <div v-if="loading" class="card text-center py-10 text-[var(--text-muted)]">加载中...</div>
    <div v-else-if="error" class="card text-rose-300">⚠️ {{ error }}</div>

    <template v-else>
      <!-- DATA_GAP 红横幅：配置采数却 0 记录 = 闸门变盲 -->
      <div v-if="gapArms.length" class="card border-rose-500/50 bg-rose-500/10">
        <div class="text-rose-300 font-medium text-sm">
          ⚠️ {{ gapArms.length }} 条风控臂处于「采数缺口」——mode=shadow/enforce 但最长窗口 0 条记录，
          闸门等同盲跑（未触发或影子路径写不进），请立即排查：
        </div>
        <div class="mt-2 flex flex-wrap gap-2">
          <span v-for="a in gapArms" :key="a.arm" class="badge badge-danger font-mono">{{ a.arm }}</span>
        </div>
      </div>

      <!-- 真实成交基线 -->
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">真实成交平仓数</div>
          <div class="text-xl font-semibold mt-1 font-mono">{{ baseline.real_closes ?? 0 }}</div>
          <div class="text-xs mt-1 text-[var(--text-muted)]">近 500 笔 forward ledger</div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">真实胜率</div>
          <div class="text-xl font-semibold mt-1 font-mono">{{ winRateTxt(baseline.real_win_rate) }}</div>
          <div class="text-xs mt-1 text-[var(--text-muted)]">真钱对照基准</div>
        </div>
        <div class="card md:col-span-2">
          <div class="text-xs text-[var(--text-muted)]">对照状态</div>
          <div class="mt-1.5 flex items-center gap-2 flex-wrap">
            <span v-if="baseline.real_closes === 0" class="badge badge-warn">无真钱对照</span>
            <span v-else class="badge badge-ok">有真钱对照</span>
            <span class="text-xs text-[var(--text-muted)]">{{ baseline.note || '评级可对照真实成交表现' }}</span>
          </div>
        </div>
      </div>

      <!-- 评级汇总 -->
      <div class="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div class="card"><div class="text-xs text-[var(--text-muted)]">采数缺口（门变盲）</div><div class="text-lg font-semibold mt-1 text-rose-400">{{ gapArms.length }}</div></div>
        <div class="card"><div class="text-xs text-[var(--text-muted)]">建议复核（疑似误伤）</div><div class="text-lg font-semibold mt-1 text-amber-300">{{ reviewArms.length }}</div></div>
        <div class="card"><div class="text-xs text-[var(--text-muted)]">可考虑升 enforce</div><div class="text-lg font-semibold mt-1 text-emerald-400">{{ promoteArms.length }}</div></div>
        <div class="card"><div class="text-xs text-[var(--text-muted)]">采集中</div><div class="text-lg font-semibold mt-1">{{ collectingArms.length }}</div></div>
        <div class="card"><div class="text-xs text-[var(--text-muted)]">已关闭（off）</div><div class="text-lg font-semibold mt-1 text-[var(--text-muted)]">{{ offArms.length }}</div></div>
      </div>

      <!-- 12 臂评级主表 -->
      <div class="card overflow-x-auto">
        <div class="text-sm font-medium mb-2 px-1">风控臂评级明细（{{ arms.length }}）</div>
        <table class="w-full text-xs">
          <thead>
            <tr class="text-left text-[var(--text-muted)] border-b border-[var(--border)]">
              <th class="py-2 px-3 font-medium whitespace-nowrap">风控臂</th>
              <th class="py-2 px-3 font-medium">模式</th>
              <th class="py-2 px-3 font-medium">类型</th>
              <th class="py-2 px-3 font-medium">评级</th>
              <th class="py-2 px-3 font-medium text-center">24h</th>
              <th class="py-2 px-3 font-medium text-center">72h</th>
              <th class="py-2 px-3 font-medium text-center">168h</th>
              <th class="py-2 px-3 font-medium whitespace-nowrap">成熟度（n/60）</th>
              <th class="py-2 px-3 font-medium">说明</th>
              <th class="py-2 px-3 font-medium text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="a in arms" :key="a.arm"
                class="border-b border-[var(--border)] last:border-0 hover:bg-[var(--surface-hover)]"
                :class="a.verdict === 'DATA_GAP' ? 'bg-rose-500/10' : ''">
              <td class="py-2.5 px-3 font-mono font-semibold whitespace-nowrap">{{ a.arm }}</td>
              <td class="py-2.5 px-3">
                <span class="badge" :class="modeBadge(a.mode)">{{ a.mode }}</span>
              </td>
              <td class="py-2.5 px-3 text-[var(--text-muted)]">{{ kindCN(a.kind) }}</td>
              <td class="py-2.5 px-3 whitespace-nowrap">
                <span class="badge" :class="verdictBadge(a.verdict)">{{ a.verdict_cn || a.verdict }}</span>
              </td>
              <td v-for="w in windowsH" :key="w" class="py-2.5 px-3 text-center align-top">
                <template v-for="s in (a.windows || []).filter((x: any) => x.window_h === w)" :key="s.window_h">
                  <div class="font-mono">{{ s.total }} 条</div>
                  <div class="text-[10px] text-[var(--text-muted)] font-mono">命中 {{ s.hits }}/{{ s.decisions }}（{{ hitRateTxt(s) }}）</div>
                  <div class="text-[10px] text-[var(--text-muted)] font-mono">回填 {{ s.mature_outcomes }}（胜{{ s.outcome_wins }}/负{{ s.outcome_losses }}）</div>
                </template>
              </td>
              <td class="py-2.5 px-3 whitespace-nowrap">
                <div class="flex items-center gap-2">
                  <div class="w-20 h-1.5 rounded-full bg-[var(--border)] overflow-hidden">
                    <div class="h-full rounded-full"
                         :class="a.verdict === 'DATA_GAP' ? 'bg-rose-500' : maturityPct(a) >= 100 ? 'bg-emerald-500' : 'bg-amber-400'"
                         :style="{ width: maturityPct(a) + '%' }"></div>
                  </div>
                  <span class="font-mono text-[10px] text-[var(--text-muted)]">{{ longestWindow(a)?.total ?? 0 }}/60</span>
                </div>
              </td>
              <td class="py-2.5 px-3 text-[var(--text-muted)] min-w-[220px]">{{ a.reason }}</td>
              <td class="py-2.5 px-3 text-right whitespace-nowrap">
                <button v-if="a.verdict === 'PROMOTE_CANDIDATE'" class="btn btn-primary text-xs"
                        @click="router.push('/config')">去升级 →</button>
                <button v-else-if="a.verdict === 'REVIEW'" class="btn btn-ghost text-xs"
                        @click="router.push('/config')">去复核</button>
                <span v-else class="text-[var(--text-muted)]">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 每晚评级历史 -->
      <div class="card overflow-x-auto">
        <div class="text-sm font-medium mb-2 px-1">
          每晚评级历史（近 30 天，{{ history.length }} 个快照；趋势图在 M5 补齐）
        </div>
        <table class="w-full text-xs">
          <thead>
            <tr class="text-left text-[var(--text-muted)] border-b border-[var(--border)]">
              <th class="py-2 px-3 font-medium">日期</th>
              <th class="py-2 px-3 font-medium text-right">真实成交</th>
              <th class="py-2 px-3 font-medium text-right">缺口</th>
              <th class="py-2 px-3 font-medium text-right">复核</th>
              <th class="py-2 px-3 font-medium text-right">可升级</th>
              <th class="py-2 px-3 font-medium text-right">采集中</th>
              <th class="py-2 px-3 font-medium text-right">关闭</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(s, i) in recentHistory" :key="i" class="border-b border-[var(--border)] last:border-0 hover:bg-[var(--surface-hover)]">
              <td class="py-2 px-3 font-mono whitespace-nowrap">{{ fmtDay(s.ts) }}</td>
              <td class="py-2 px-3 font-mono text-right">{{ s.real_closes ?? 0 }}</td>
              <td class="py-2 px-3 font-mono text-right text-rose-400">{{ snapshotCounts(s).DATA_GAP || 0 }}</td>
              <td class="py-2 px-3 font-mono text-right text-amber-300">{{ snapshotCounts(s).REVIEW || 0 }}</td>
              <td class="py-2 px-3 font-mono text-right text-emerald-400">{{ snapshotCounts(s).PROMOTE_CANDIDATE || 0 }}</td>
              <td class="py-2 px-3 font-mono text-right">{{ (snapshotCounts(s).INSUFFICIENT_DATA || 0) + (snapshotCounts(s).COLLECTING || 0) }}</td>
              <td class="py-2 px-3 font-mono text-right text-[var(--text-muted)]">{{ snapshotCounts(s).OFF || 0 }}</td>
            </tr>
            <tr v-if="recentHistory.length === 0">
              <td colspan="7" class="py-8 text-center text-[var(--text-muted)]">
                暂无每晚评级快照（夜间 cron 首跑后生成；手动「立即重评」不写历史）
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- INERT 红线声明 -->
      <div class="card text-xs text-[var(--text-muted)] leading-5">
        🔒 评级器为只读建议面：只评级并经夜间飞书卡片推送建议，<strong>绝不自动改配置、不改闸门 mode、不下单</strong>。
        PROMOTE_CANDIDATE 仅为建议，升 enforce 须人工在「系统配置」页走 config_store 权威写路径；
        「去升级/去复核」按钮仅跳转配置页，本页不含任何改闸门或交易控件。
      </div>
    </template>
  </div>
</template>
