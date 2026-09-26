<script setup lang="ts">
/**
 * 持仓管理
 * - 数据来源：hermes-trader /api/dashboard/positions（含杠杆/ROE/现货涨跌/DSL 阶段）
 * - 支持手动平仓（走 /api/hl/close-position）
 * - 汇总卡片：持仓数 / 总未实现盈亏 / 做多 / 做空
 */
import { computed, onMounted, onUnmounted, ref } from 'vue';
import http from '@/shared/api/client';
import { useAuthStore } from '@/stores/auth';
import { useToast } from '@/stores/toast';
import { useConfirmStore } from '@/stores/confirm';
import { useSseFeedStore } from '@/stores/sseFeed';
import { liqDistPct, liqLevel } from '@/shared/utils/liquidation';

const auth = useAuthStore();
const toast = useToast();
const confirmStore = useConfirmStore();

const loading = ref(true);
const error = ref('');
const stale = ref(false);
const positions = ref<any[]>([]);
const closing = ref<string | null>(null);
let timer: number | null = null;
let sseUnsub: (() => void) | null = null;
let pendingRefresh: number | null = null;

// 平仓走 /api/hl/close-position，BFF 权威权限码为 trade:close（与开仓 trade:execute 区分）。
const canClose = computed(() => auth.hasPermission('trade:close'));

// SSE 事件驱动的刷新调度：将连续 position_update 事件合并为单次 load()，
// 避免 burst 事件触发多次 HTTP 请求。500ms 内的重复事件只触发一次拉取。
function scheduleRefresh() {
  if (pendingRefresh !== null) return;
  pendingRefresh = window.setTimeout(() => {
    pendingRefresh = null;
    load();
  }, 500);
}

async function load() {
  try {
    error.value = '';
    const res = await http.get('/api/portal/trader/api/dashboard/positions');
    // 服务端 stale-if-error 兜底生效时携带 X-Positions-Stale，标记数据为缓存快照
    stale.value = res.headers?.['x-positions-stale'] === '1';
    const data = res.data;
    const incoming: any[] = Array.isArray(data) ? data : (data?.positions || []);
    // 按 coin 做浅 diff：未变化的行保留原对象引用，避免 Vue 整表重渲染
    const prev = new Map(positions.value.map((p) => [p.coin, p]));
    positions.value = incoming.map((np) => {
      const old = prev.get(np.coin);
      if (!old) return np;
      const keys = Object.keys(np);
      let changed = keys.length !== Object.keys(old).length;
      if (!changed) {
        for (const k of keys) {
          if (old[k] !== np[k]) { changed = true; break; }
        }
      }
      return changed ? { ...old, ...np } : old;
    });
  } catch (e: any) {
    // 刷新失败保留上次数据继续渲染（表格上方横幅提示），
    // 仅首次加载就失败（无任何数据）时才整屏报错
    error.value = e?.response?.data?.detail || '加载持仓失败';
    if (positions.value.length) stale.value = true;
  } finally {
    loading.value = false;
  }
}

async function closePosition(coin: string) {
  if (!canClose.value) return;
  if (!(await confirmStore.confirm({ message: `确定平仓 ${coin}？`, danger: true, confirmText: '平仓' }))) return;
  closing.value = coin;
  try {
    // 后端根据 coin 自动查找当前持仓方向并市价平仓，无需传 size
    await http.post('/api/portal/trader/api/hl/close-position', { coin });
    toast.ok(`${coin} 平仓指令已提交`);
    await load();
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '平仓失败');
  } finally {
    closing.value = null;
  }
}

function fmt(v: any, d = 4) {
  if (v === null || v === undefined || isNaN(Number(v))) return '—';
  return Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}
function fmtPct(v: any) {
  if (v === null || v === undefined || isNaN(Number(v))) return '—';
  const n = Number(v);
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
}
function pnlColor(v: number | undefined | null) {
  if (v === null || v === undefined) return 'text-[var(--text-muted)]';
  if (v > 0) return 'text-emerald-400';
  if (v < 0) return 'text-rose-400';
  return '';
}

// 距强平距离着色（与强平价预警卡片同一阈值）：<10% 红、<20% 琥珀，其余常态
function liqDistColor(p: any) {
  const lv = liqLevel(liqDistPct(p));
  if (lv === 'danger') return 'text-rose-400';
  if (lv === 'warn') return 'text-amber-400';
  return 'text-[var(--text-muted)]';
}

// 汇总
const totalUPnL = computed(() => positions.value.reduce((s, p) => s + (Number(p.unrealized_pnl_usd) || 0), 0));
const longCount = computed(() => positions.value.filter((p) => p.side === 'long').length);
const shortCount = computed(() => positions.value.filter((p) => p.side === 'short').length);

function dslBadgeClass(dsl: any) {
  if (!dsl) return '';
  return dsl.phase === 'phase2' ? 'badge-ok' : 'badge-warn';
}
function dslLabel(dsl: any) {
  if (!dsl) return '—';
  return dsl.phase === 'phase2' ? '阶段2' : '阶段1';
}

onMounted(() => {
  load();
  // SSE 事件驱动刷新：position_update 到达时立即触发拉取（防抖 500ms），
  // 将持仓感知延迟从"等下一轮 10s 轮询"降到 SSE transit + 500ms。
  const sse = useSseFeedStore();
  sseUnsub = sse.onEvent((data) => {
    if (data?.event === 'position_update') scheduleRefresh();
  });
  // 60s 兜底轮询：SSE 断连或事件丢失时仍能保持数据新鲜
  timer = window.setInterval(load, 60000);
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
    <header class="flex items-center justify-between">
      <div>
        <h2 class="text-xl font-semibold">当前持仓</h2>
        <p class="text-sm text-[var(--text-muted)] mt-1">实时持仓与盈亏（SSE 事件驱动 + 60s 兜底）</p>
      </div>
      <button class="btn" @click="load">🔄 刷新</button>
    </header>

    <div v-if="loading" class="card text-center py-10 text-[var(--text-muted)]">加载中...</div>
    <div v-else-if="error && positions.length === 0" class="card text-rose-300">⚠️ {{ error }}</div>

    <template v-else>
      <!-- 刷新失败 / 服务端缓存快照：保留上次数据，横幅提示 -->
      <div v-if="error || stale" class="card border-amber-500/40 bg-amber-500/10 text-amber-300 text-sm py-2">
        ⚠️ {{ error ? `刷新失败（${error}），展示最近一次成功数据` : '上游连接异常，当前为服务端缓存快照' }}
      </div>
      <!-- 汇总卡片 -->
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">持仓数</div>
          <div class="text-2xl font-semibold mt-1 text-purple-400">{{ positions.length }}</div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">总未实现盈亏</div>
          <div class="text-2xl font-semibold mt-1" :class="pnlColor(totalUPnL)">
            {{ totalUPnL >= 0 ? '+' : '' }}${{ fmt(totalUPnL, 2) }}
          </div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">做多</div>
          <div class="text-2xl font-semibold mt-1 text-emerald-400">{{ longCount }}</div>
        </div>
        <div class="card">
          <div class="text-xs text-[var(--text-muted)]">做空</div>
          <div class="text-2xl font-semibold mt-1 text-rose-400">{{ shortCount }}</div>
        </div>
      </div>

      <!-- 持仓表格 -->
      <div class="card overflow-x-auto">
        <table class="w-full text-xs">
          <thead>
            <tr class="text-left text-[var(--text-muted)] border-b border-[var(--border)]">
              <th class="py-2 px-3 font-medium">币种</th>
              <th class="py-2 px-3 font-medium">方向</th>
              <th class="py-2 px-3 font-medium text-right hidden md:table-cell">仓位</th>
              <th class="py-2 px-3 font-medium text-right hidden md:table-cell">杠杆</th>
              <th class="py-2 px-3 font-medium text-right">开仓价</th>
              <th class="py-2 px-3 font-medium text-right hidden md:table-cell">标记价</th>
              <th class="py-2 px-3 font-medium text-right">强平价</th>
              <th class="py-2 px-3 font-medium text-right">未实现盈亏</th>
              <th class="py-2 px-3 font-medium text-right hidden md:table-cell" title="含杠杆的仓位回报率（未实现盈亏 / 占用保证金），Hyperliquid 口径，已计入开仓手续费">
                ROE %<span class="opacity-60">ⓘ</span>
              </th>
              <th class="py-2 px-3 font-medium text-right hidden md:table-cell" title="标的价格本身相对开仓价的涨跌幅，未乘杠杆；ROE% ≈ 价格涨跌% × 杠杆（扣费前）">
                价格涨跌%<span class="opacity-60">ⓘ</span>
              </th>
              <th class="py-2 px-3 font-medium text-center">DSL</th>
              <th class="py-2 px-3 font-medium text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in positions" :key="p.coin" class="border-b border-[var(--border)] last:border-0 hover:bg-[var(--surface-hover)]">
              <td class="py-2.5 px-3 font-mono font-semibold">{{ p.coin }}</td>
              <td class="py-2.5 px-3">
                <span :class="p.side === 'long' ? 'badge-ok' : 'badge-danger'">{{ p.side === 'long' ? '做多' : '做空' }}</span>
              </td>
              <td class="py-2.5 px-3 font-mono text-right hidden md:table-cell">{{ fmt(p.size, 4) }}</td>
              <td class="py-2.5 px-3 font-mono text-right text-[var(--text-muted)] hidden md:table-cell">{{ p.leverage || 1 }}x</td>
              <td class="py-2.5 px-3 font-mono text-right">{{ fmt(p.entry_px, 4) }}</td>
              <td class="py-2.5 px-3 font-mono text-right hidden md:table-cell">{{ fmt(p.mark_px, 4) }}</td>
              <td class="py-2.5 px-3 font-mono text-right">
                <template v-if="p.liq_px != null">
                  <div>{{ fmt(p.liq_px, 2) }}</div>
                  <div class="text-[10px]" :class="liqDistColor(p)">
                    距强平 {{ liqDistPct(p)?.toFixed(1) }}%
                  </div>
                </template>
                <span v-else class="text-[var(--text-muted)]">—</span>
              </td>
              <td class="py-2.5 px-3 font-mono text-right font-medium" :class="pnlColor(p.unrealized_pnl_usd)">
                {{ p.unrealized_pnl_usd >= 0 ? '+' : '' }}${{ fmt(p.unrealized_pnl_usd, 2) }}
              </td>
              <td class="py-2.5 px-3 font-mono text-right hidden md:table-cell" :class="pnlColor(p.unrealized_pct)">
                {{ fmtPct(p.unrealized_pct) }}
              </td>
              <td class="py-2.5 px-3 font-mono text-right hidden md:table-cell" :class="pnlColor(p.spot_pct)">
                {{ fmtPct(p.spot_pct) }}
              </td>
              <td class="py-2.5 px-3 text-center">
                <span v-if="p.dsl" :class="dslBadgeClass(p.dsl)">{{ dslLabel(p.dsl) }}</span>
                <span v-else class="text-[var(--text-muted)]">—</span>
              </td>
              <td class="py-2.5 px-3 text-right">
                <button
                  class="btn btn-danger text-xs"
                  :disabled="!canClose || closing === p.coin"
                  @click="closePosition(p.coin)"
                >
                  {{ closing === p.coin ? '平仓中...' : '平仓' }}
                </button>
              </td>
            </tr>
            <tr v-if="positions.length === 0">
              <td colspan="12" class="py-10 text-center text-[var(--text-muted)]">
                当前没有持仓
                <div class="mt-1 text-xs">开仓后将在此实时显示</div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
