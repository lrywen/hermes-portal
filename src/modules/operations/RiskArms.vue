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
import { computed, onMounted, onUnmounted, ref, shallowRef } from 'vue';
import { useRouter } from 'vue-router';
import http from '@/shared/api/client';
import { useAuthStore } from '@/stores/auth';
import { useToast } from '@/stores/toast';
import { useSseFeedStore } from '@/stores/sseFeed';
import VChart from 'vue-echarts';
import { use } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components';

// M5: 评级趋势图（与 Overview.vue 同一 vue-echarts 按需注册范式）
use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent]);

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
// 历史回测证据（离线 K 线反事实回放聚合，/backfill-summary）
const backfill = ref<any>(null);
// 辩论影子 A/B（单 LLM vs bull/bear 辩论对照，/debate-ab）
const debateAb = ref<any>(null);
// 决策复盘（平仓后异步生成的定性复盘，/reflections）
const reflections = ref<any[]>([]);
// 两个观察口默认折叠（非主评级内容，按需展开）
const showDebateAb = ref(false);
const showReflections = ref(false);

// M5 趋势图 option（shallowRef：ECharts 大对象不走深响应）
const verdictOption = shallowRef<any>({});
const hitRateOption = shallowRef<any>({});

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
    const [g, h, b, rg, rs, dab, rf] = await Promise.all([
      http.get(`${API}/grades`, { params: { windows: WINDOWS } }),
      // M9：趋势只看夜间 cron 快照，手动重评/手工追加（source=manual）不污染趋势
      http.get(`${API}/grade-history`, { params: { days: 30, limit: 400, source: 'cron' } }),
      // 历史回测失败（如无产物/端点不可用）不拖垮主评级面板
      http.get(`${API}/backfill-summary`).catch(() => null),
      // 长周期信号再生回放报告 + 手动回放运行状态（失败同样不拖垮主面板）
      http.get(`${API}/regen-report`).catch(() => null),
      http.get(`${API}/regen-status`).catch(() => null),
      // 辩论影子 A/B + 决策复盘（观察口，失败不拖垮主面板）
      http.get(`${API}/debate-ab`, { params: { days: 30 } }).catch(() => null),
      http.get(`${API}/reflections`, { params: { limit: 20 } }).catch(() => null),
    ]);
    report.value = g.data;
    history.value = Array.isArray(h.data?.snapshots) ? h.data.snapshots : [];
    backfill.value = b?.data ?? null;
    regen.value = rg?.data ?? null;
    regenStatus.value = rs?.data ?? null;
    debateAb.value = dab?.data ?? null;
    reflections.value = Array.isArray(rf?.data?.reflections) ? rf.data.reflections : [];
    // 页面打开时若有在跑的回放（其他入口触发），接管轮询直到完成
    if (regenStatus.value?.running) startRegenPoll();
    buildCharts();
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
// M1：enforce 降级复核与 shadow REVIEW 分开汇总，但同属「需人工关注」
const degradedArms = computed(() => arms.value.filter((a) => a.verdict === 'ENFORCE_DEGRADED_REVIEW'));
const reviewArms = computed(() => arms.value.filter((a) => a.verdict === 'REVIEW'));
const promoteArms = computed(() => arms.value.filter((a) => a.verdict === 'PROMOTE_CANDIDATE'));
const maintainArms = computed(() => arms.value.filter((a) => a.verdict === 'ENFORCE_MAINTAIN'));
const collectingArms = computed(() =>
  arms.value.filter((a) => a.verdict === 'INSUFFICIENT_DATA' || a.verdict === 'COLLECTING'),
);
const offArms = computed(() => arms.value.filter((a) => a.verdict === 'OFF'));
// M13：采数停滞（近 24h 0 写入）
const stalledArms = computed(() => arms.value.filter((a) => a.collection_stalled));

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
  ENFORCE_DEGRADED_REVIEW: 'badge-warn',
  REVIEW: 'badge-warn',
  PROMOTE_CANDIDATE: 'badge-ok',
  ENFORCE_MAINTAIN: 'badge-ok',
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
const KIND_CN: Record<string, string> = { block: '拦截', change: '调整', signal: '信号' };
function kindCN(k: string) {
  return KIND_CN[k] || k || '—';
}
// 风控臂中文名（明细小字）+ 悬浮详细说明
const ARM_CN: Record<string, string> = {
  pullback: '回踩低吸做多信号',
  ta_late_entry: '4h 末端追高/追杀拦截',
  atr_regime_calib: '按波动率校准止损宽度',
  sizing_v2: '等风险仓位 v2（改名义本金）',
  confidence_decay: 'AI 置信度时效衰减',
  market_circuit: '极端行情全局熔断建议',
  signal_age_decay: '技术信号棒龄衰减',
  daily_extension_cap: '24h 涨幅硬顶（30%）',
  reentry_cap: '单币 24h 开仓次数上限',
  trend_filter_200ma: '日线 200SMA 趋势闸门',
  xs_reversal: '超卖反弹纯观察信号',
  regime_overlay: '震荡市自动降险姿态',
};
const ARM_TITLE: Record<string, string> = {
  pullback: '上升趋势中有序回踩、未超买时的做多旁路信号（买跌不买涨，仅做多）',
  ta_late_entry: '下单前重拉 4h K 线：RSI>75 或偏离 EMA21 超 2.5×ATR 即拦（做空镜像），强趋势放宽',
  atr_regime_calib: '当前 ATR%/近 30 天均值：波动压缩收紧止损、扩张放宽止损，仅改反推仓位的止损宽度',
  sizing_v2: '与 DSL 三层止损对齐的等风险仓位公式，按 v2 止损宽度重算名义本金',
  confidence_decay: '同一 AI 裁决随年龄指数衰减置信度（半衰期 15min），防缓存回放过期高置信观点',
  market_circuit: 'BTC/ETH 闪崩、多币 180s 内成群止损、资金费率极端时建议账户级全局停机',
  signal_age_decay: '技术形态触发器综合分按首次触发棒龄衰减（半衰期 15min~2h），防旧突破以新高分浮现',
  daily_extension_cap: '24h 涨幅超 30% 拦做多（只拦多），专拦抛物线追高',
  reentry_cap: '同一币 24h 内开仓 ≥2 次则拦新入场，压制止损后反复买回的 churn',
  trend_filter_200ma: '价格在日线 200SMA 下方拦做多；24h 涨幅 10%~30% 的强势日内币可旁路',
  xs_reversal: '下跌趋势中深度回撤+活跃+RSI 超卖区间的反弹做多探针，仅观察不下单',
  regime_overlay: '宏观连续判为震荡时单向收紧姿态（限并发/禁空/半仓/关回踩），趋势恢复自动解除',
};
function armCN(a: string) {
  return ARM_CN[a] || '';
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
// ISO 时间戳 → 月-日 时:分
function fmtTs(ts: any): string {
  if (!ts) return '—';
  const d = new Date(ts);
  if (isNaN(d.getTime())) return '—';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
// {LONG:3,PASS:1} → "LONG 3 · PASS 1"
function verdictDistTxt(dist: any): string {
  const entries = Object.entries(dist || {});
  if (!entries.length) return '—';
  return entries.map(([k, v]) => `${k} ${v}`).join(' · ');
}
// single/debate 子对象 → "verdict conf"
function verdictCell(v: any): string {
  if (!v || !v.verdict) return '—';
  const conf = v.confidence === null || v.confidence === undefined
    ? '' : ` ${Number(v.confidence).toFixed(2)}`;
  return `${v.verdict}${conf}`;
}
function snapshotCounts(snap: any) {
  const c: Record<string, number> = {};
  for (const a of snap?.arms || []) c[a.verdict] = (c[a.verdict] || 0) + 1;
  return c;
}
// M4：最长窗 outcome 回填率（mature/total）
function backfillTxt(a: any) {
  const r = Number(a?.backfill_rate ?? NaN);
  return isNaN(r) ? '—' : (r * 100).toFixed(1) + '%';
}

// -------------------------------------------------- 历史回测证据（backfill）
const backfillArms = computed<any[]>(() => backfill.value?.arms || []);
const backfillPresent = computed(() => backfillArms.value.filter((a) => a.present));

function pctTxt(v: any): string {
  const x = Number(v);
  return isNaN(x) ? '—' : (x > 0 ? '+' : '') + x.toFixed(2) + '%';
}
function pctClass(v: any): string {
  const x = Number(v);
  if (isNaN(x)) return 'text-[var(--text-muted)]';
  return x > 0 ? 'text-emerald-400' : x < 0 ? 'text-rose-400' : 'text-[var(--text-muted)]';
}
function wrTxt(v: any): string {
  const x = Number(v);
  return isNaN(x) ? '—' : (x * 100).toFixed(1) + '%';
}
function isoDay(v: any): string {
  return String(v || '').slice(0, 10) || '—';
}
// 臂特定证据行：xs 前瞻收益 / atr 校准差值 / per_coin_regime 处置分布
function extrasTxt(a: any): string[] {
  const e = a?.extras || {};
  const out: string[] = [];
  if (a.arm === 'xs_reversal') {
    out.push(`严格候选 ${e.candidates ?? 0}/${a.records}`);
    for (const h of ['24h', '72h', '168h']) {
      const f = e.forward?.[h];
      if (f) out.push(`${h} 胜率 ${wrTxt(f.win_rate)} · 均 ${pctTxt(f.avg_pct)}`);
    }
  } else if (a.arm === 'atr_regime_calib') {
    out.push(`会改参 ${e.would_change ?? 0}/${a.records}`);
    const d = e.calibration_delta;
    if (d) out.push(`v2-v1 均 ${pctTxt(d.avg_pct)}（改善 ${d.improved}/${d.n}）`);
  } else if (a.arm === 'per_coin_regime') {
    const w = e.would || {};
    const s = Object.entries(w).map(([k, v]) => `${k}×${v}`).join(' · ');
    if (s) out.push(s);
  }
  return out;
}
// 分母修正后的命中集有害率（未给出口径时回退展示全记录有害率）
function hitHarmTxt(a: any) {
  const r = a?.hit_set_harmful_rate;
  if (r === null || r === undefined || isNaN(Number(r))) return '—';
  return (Number(r) * 100).toFixed(1) + '%';
}
// Audit 2026-09-12：实质性反事实合计（|反事实 pnl|≥$0.5 的命中成熟笔合计）。
// 正=采纳臂净亏钱（臂有害），负=采纳臂净省钱（臂有益）；仅写 pnl_usd 的臂有此字段，
// pct-only 臂（atr/confidence 等）返回 null。
function materialSumTxt(a: any): string | null {
  if (!a?.harmful_rate_money_basis) return null;
  const v = a?.hit_set_material_pnl_sum;
  if (v === null || v === undefined || isNaN(Number(v))) return null;
  const n = Number(a?.hit_set_material_n ?? 0);
  if (n === 0) return null; // 零实质性笔交给单元格的「无实质性笔」分支
  const sign = Number(v) > 0 ? '+' : '';
  return `${sign}$${Number(v).toFixed(2)}（${n} 笔）`;
}
const SV2_CHECK_CN: Record<string, string> = {
  c1_sample_per_side: '分方向样本',
  c2_source_maturity: '来源成熟度',
  c3_cap_bind_rate: '上限绑定率',
  c4_ratio_sanity: '比率合理性',
  c5_carry_check: 'carry校验',
  c6_zero_side_effects: '零副作用',
};

// -------------------------------------------------- 长周期信号再生回放（regen）
// 数据源：scripts/regen_param_sweep.py --write 产物（GET /regen-report，60s 缓存，
// 重跑后 generated_at/mtime 自动更新）。与上方 backfill（补 outcome 口径）不同：
// 本卡是「信号再生 + 反事实拦截回放」，仅适用 ta_late_entry / trend_filter_200ma /
// daily_extension_cap 三臂；train/val/test 三段 walk-forward，EV 为每笔净期望（含 5bps 费用）。
// 「拦截受益/笔」= -(被拦单 EV)：正=拦掉的尽是亏钱单（臂有益），负=误伤。
const regen = ref<any>(null);
const regenStatus = ref<any>(null);
const regenDays = ref(120);
const regenCoins = ref('');
const regenTriggering = ref(false);
let regenPoll: number | null = null;

const regenPresent = computed(() => !!regen.value?.present);
const regenRunning = computed(() => !!regenStatus.value?.running);
const regenOverlap = computed<any>(() => regen.value?.overlap || {});
const regenPicks = computed<any>(() => regen.value?.plateau_picks || {});
const regenWindow = computed<any>(() => regen.value?.window || {});
const taSweepTop = computed<any[]>(() => regen.value?.ta_late_entry_sweep_top || []);
const trendSweep = computed<any[]>(() => regen.value?.trend_filter_sweep || []);
const capSweep = computed<any[]>(() => regen.value?.daily_ext_cap_sweep || []);
const relaxTierRows = computed<any[]>(() =>
  Object.entries(regen.value?.relax_tier || {}).map(([tier, v]: [string, any]) => ({ tier, ...v })),
);

const AXIS_CN: Record<string, string> = {
  rsi_ob: 'RSI 超买线',
  ext_ob: 'EMA 偏离倍数',
  adx_floor: 'ADX 趋势门槛',
};
const axisRows = computed<any[]>(() => {
  const base = regen.value?.params_baseline || {};
  const curves = regen.value?.axis_curves || {};
  return Object.entries(curves).map(([axis, points]: [string, any]) => ({
    axis,
    cn: AXIS_CN[axis] || axis,
    baseline: base[axis],
    pick: regenPicks.value?.[axis],
    points: Array.isArray(points) ? points : [],
  }));
});

// 三臂 sweep 统一表结构：仅参数标签不同，train/val/test 口径一致
const sweepTables = computed<any[]>(() => {
  const t: any[] = [];
  if (taSweepTop.value.length) {
    t.push({
      key: 'ta',
      title: `ta_late_entry 参数网格 Top ${taSweepTop.value.length} / ${regen.value?.ta_late_entry_sweep_rows ?? 0}（按 val 拦截受益排序，小样本格排底）`,
      rows: taSweepTop.value,
      label: (r: any) => {
        const p = r.params || {};
        return `RSI≥${p.rsi_ob} · 偏离≥${p.ext_ob}×ATR · ADX≥${p.adx_floor}${p.relax ? ' · 强趋势放宽' : ''}`;
      },
    });
  }
  if (trendSweep.value.length) {
    t.push({
      key: 'tf',
      title: 'trend_filter_200ma 强势币旁路窗口（mover_window，24h 涨幅%）',
      rows: trendSweep.value,
      label: (r: any) => `涨幅 ${r.mover_window}`,
    });
  }
  if (capSweep.value.length) {
    t.push({
      key: 'cap',
      title: 'daily_extension_cap 24h 涨幅硬顶（cap %）',
      rows: capSweep.value,
      label: (r: any) => `≥${r.cap}% 拦多`,
    });
  }
  if (relaxTierRows.value.length) {
    t.push({
      key: 'rt',
      title: 'relax_tier 弱趋势放宽探针（分层档位）',
      rows: relaxTierRows.value,
      label: (r: any) => r.tier,
    });
  }
  return t;
});

function fmtWinMs(ms: any): string {
  const t = Number(ms);
  if (!isFinite(t) || t <= 0) return '—';
  return new Date(t).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}
function usdTxt(v: any): string {
  const x = Number(v);
  return isNaN(x) ? '—' : (x > 0 ? '+' : '') + '$' + x.toFixed(2);
}
function avoidedTxt(split: any): string {
  return usdTxt(split?.avoided_loss_per_block);
}
function avoidedClass(split: any): string {
  const v = Number(split?.avoided_loss_per_block);
  if (isNaN(v)) return 'text-[var(--text-muted)]';
  return v > 0 ? 'text-emerald-400' : v < 0 ? 'text-rose-400' : 'text-[var(--text-muted)]';
}
function agreeTxt(v: any): string {
  const x = Number(v);
  return isNaN(x) ? '—' : (x * 100).toFixed(2) + '%';
}

// 手动触发回放：POST regen-refresh → 轮询 regen-status 直到完成 → 重新加载报告
async function triggerRegen() {
  if (!canRefresh.value || regenTriggering.value || regenRunning.value) return;
  regenTriggering.value = true;
  try {
    const body: any = { days: regenDays.value };
    if (regenCoins.value.trim()) body.coins = regenCoins.value.trim();
    await http.post(`${API}/regen-refresh`, body);
    toast.ok(`已触发 ${regenDays.value} 天信号再生回放（后台运行，完成后自动刷新本卡）`);
    startRegenPoll();
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '触发回放失败');
  } finally {
    regenTriggering.value = false;
  }
}
async function pollRegenStatusOnce() {
  try {
    const { data } = await http.get(`${API}/regen-status`);
    regenStatus.value = data;
    return data;
  } catch {
    return null;
  }
}
function startRegenPoll() {
  if (regenPoll !== null) return;
  const tick = async () => {
    const st = await pollRegenStatusOnce();
    if (st && !st.running) {
      stopRegenPoll();
      if (st.exit_code === 0) {
        toast.ok('回放完成，最新回测报告已更新');
        loadAll(false);
      } else {
        toast.err(`回放失败：${st.error || 'exit ' + st.exit_code}`);
      }
      return;
    }
    regenPoll = window.setTimeout(tick, 5000);
  };
  regenPoll = window.setTimeout(tick, 3000);
}
function stopRegenPoll() {
  if (regenPoll !== null) {
    window.clearTimeout(regenPoll);
    regenPoll = null;
  }
}

// ---------------------------------------------------------------- M5 趋势图
const AXIS_STYLE = { axisLine: { lineStyle: { color: '#232A3B' } }, axisLabel: { color: '#6B7280', fontSize: 10 }, splitLine: { show: false } };
const TOOLTIP_STYLE = { backgroundColor: '#0B0E14', borderColor: '#232A3B', textStyle: { color: '#E5E7EB', fontSize: 11 } };

// M5: 由 grade-history 快照构建两张趋势图：
//   1) 每晚评级分布堆叠面积（DATA_GAP/REVIEW/PROMOTE/采集中/OFF 条数走势）
//   2) 当前 enforce/shadow 各臂命中率走势（长窗 hit_rate，0 决策的点置 null 断线）
function buildCharts() {
  const snaps = history.value;
  if (!snaps.length) {
    verdictOption.value = {};
    hitRateOption.value = {};
    return;
  }
  const xData = snaps.map((s) => fmtDay(s.ts));

  const VERDICT_SERIES: [string, string, string][] = [
    ['DATA_GAP', '采数缺口', '#F43F5E'],
    ['ENFORCE_DEGRADED_REVIEW', 'enforce降级复核', '#FB923C'],
    ['REVIEW', '建议复核', '#F59E0B'],
    ['PROMOTE_CANDIDATE', '可升级', '#10B981'],
    ['ENFORCE_MAINTAIN', 'enforce维持', '#34D399'],
    ['COLLECTING', '采集中', '#818CF8'],
    ['OFF', '已关闭', '#64748B'],
  ];
  verdictOption.value = {
    backgroundColor: 'transparent',
    grid: { left: 40, right: 16, top: 28, bottom: 28 },
    tooltip: { trigger: 'axis', ...TOOLTIP_STYLE },
    legend: { textStyle: { color: '#9CA3AF', fontSize: 10 }, top: 0, itemWidth: 12, itemHeight: 8 },
    xAxis: { type: 'category', data: xData, ...AXIS_STYLE },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#1E2433' } }, axisLabel: { color: '#6B7280', fontSize: 10 } },
    series: VERDICT_SERIES.map(([key, name, color]) => ({
      name,
      type: 'line',
      stack: 'verdict',
      smooth: true,
      showSymbol: false,
      areaStyle: { opacity: 0.25 },
      lineStyle: { width: 1 },
      itemStyle: { color },
      // INSUFFICIENT_DATA 并入采集中（与历史表口径一致）；新档位在旧快照中计数为 0
      data: snaps.map((s) => (snapshotCounts(s)[key] || 0) + (key === 'COLLECTING' ? (snapshotCounts(s).INSUFFICIENT_DATA || 0) : 0)),
    })),
  };

  // 各臂命中率：以最新评级里非 OFF 的臂为序列集合
  const activeArms = arms.value.filter((a) => a.mode !== 'off').map((a) => a.arm);
  hitRateOption.value = {
    backgroundColor: 'transparent',
    grid: { left: 44, right: 16, top: 28, bottom: 28 },
    tooltip: {
      trigger: 'axis',
      ...TOOLTIP_STYLE,
      valueFormatter: (v: any) => (v === null || v === undefined ? '—' : (Number(v) * 100).toFixed(1) + '%'),
    },
    legend: { textStyle: { color: '#9CA3AF', fontSize: 10 }, top: 0, type: 'scroll', itemWidth: 12, itemHeight: 8 },
    xAxis: { type: 'category', data: xData, ...AXIS_STYLE },
    yAxis: {
      type: 'value',
      min: 0,
      max: 1,
      splitLine: { lineStyle: { color: '#1E2433' } },
      axisLabel: { color: '#6B7280', fontSize: 10, formatter: (v: number) => `${Math.round(v * 100)}%` },
    },
    series: activeArms.map((arm) => ({
      name: arm,
      type: 'line',
      smooth: true,
      showSymbol: false,
      connectNulls: false,
      data: snaps.map((s) => {
        const sa = (s.arms || []).find((x: any) => x.arm === arm);
        return sa && Number(sa.decisions ?? 0) > 0 ? Number(sa.hit_rate ?? 0) : null;
      }),
    })),
  };
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
  stopRegenPoll();
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

      <!-- M13：采数停滞横幅（近 24h 0 写入，事件驱动型闸门停采或写路径异常） -->
      <div v-if="stalledArms.length" class="card border-amber-500/50 bg-amber-500/10">
        <div class="text-amber-300 font-medium text-sm">
          ⏸ {{ stalledArms.length }} 条风控臂「采数停滞」——最长窗有历史记录但近 24h 0 条写入，
          疑似事件驱动型闸门停采或写路径异常：
        </div>
        <div class="mt-2 flex flex-wrap gap-2">
          <span v-for="a in stalledArms" :key="a.arm"
                class="badge badge-warn font-mono" :title="a.collection_stalled?.stale_hours + 'h 无新记录'">
            {{ a.arm }}<span class="ml-1 opacity-80">{{ a.collection_stalled?.stale_hours }}h</span>
          </span>
        </div>
      </div>

      <!-- M1：enforce 臂健康告警（已在生产却出现拦太宽/高误伤，建议复核降级） -->
      <div v-if="degradedArms.length" class="card border-orange-500/50 bg-orange-500/10">
        <div class="text-orange-300 font-medium text-sm">
          🔶 {{ degradedArms.length }} 条已 enforce 臂出现健康告警（拦/改太宽或臂有害率越红线），
          评级器仅建议人工复核降级，<strong>不会自动改 mode</strong>：
        </div>
        <div class="mt-2 flex flex-wrap gap-2">
          <span v-for="a in degradedArms" :key="a.arm" class="badge badge-warn font-mono">{{ a.arm }}</span>
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

      <!-- 辩论影子 A/B（观察口，默认折叠） -->
      <div class="card">
        <button class="w-full flex items-center justify-between text-sm font-medium px-1"
                @click="showDebateAb = !showDebateAb">
          <span>辩论影子 A/B · bull/bear 辩论 vs 单 LLM 对照</span>
          <span class="text-xs text-[var(--text-muted)]">{{ showDebateAb ? '收起 ▲' : '展开 ▼' }}</span>
        </button>
        <div v-if="showDebateAb" class="mt-3">
          <div v-if="!debateAb || debateAb.sample === 0"
               class="text-xs text-[var(--text-muted)] px-1">近 30 天暂无 A/B 对照样本。</div>
          <template v-else>
            <div class="grid grid-cols-2 md:grid-cols-3 gap-3 mb-3">
              <div><div class="text-xs text-[var(--text-muted)]">对照样本</div>
                <div class="text-lg font-semibold font-mono">{{ debateAb.sample }}</div></div>
              <div><div class="text-xs text-[var(--text-muted)]">verdict 一致率</div>
                <div class="text-lg font-semibold font-mono">{{ pctTxt(debateAb.agreement_rate) }}</div></div>
              <div class="col-span-2 md:col-span-1">
                <div class="text-xs text-[var(--text-muted)]">单 LLM / 辩论 verdict 分布</div>
                <div class="text-xs mt-1 font-mono leading-5">
                  {{ verdictDistTxt(debateAb.single_verdicts) }}<br>
                  {{ verdictDistTxt(debateAb.debate_verdicts) }}
                </div>
              </div>
            </div>
            <div class="overflow-x-auto">
              <table class="w-full text-xs">
                <thead>
                  <tr class="text-left text-[var(--text-muted)] border-b border-[var(--border)]">
                    <th class="py-2 px-2 font-medium">时间</th>
                    <th class="py-2 px-2 font-medium">币种</th>
                    <th class="py-2 px-2 font-medium text-center">单 LLM</th>
                    <th class="py-2 px-2 font-medium text-center">辩论</th>
                    <th class="py-2 px-2 font-medium text-center">一致</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(r, i) in debateAb.rows.slice().reverse().slice(0, 50)" :key="i"
                      class="border-b border-[var(--border)]">
                    <td class="py-1.5 px-2 whitespace-nowrap text-[var(--text-muted)]">{{ fmtTs(r.ts) }}</td>
                    <td class="py-1.5 px-2 font-mono">{{ r.coin }}</td>
                    <td class="py-1.5 px-2 text-center">{{ verdictCell(r.single) }}</td>
                    <td class="py-1.5 px-2 text-center">{{ verdictCell(r.debate) }}</td>
                    <td class="py-1.5 px-2 text-center">{{ r.agree ? '✓' : '·' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p class="text-[11px] text-[var(--text-muted)] mt-2 px-1">
              辩论结论仅作对照，不参与实际下单；一致率/分歧模式用于判断是否值得把辩论从影子提升为生产路径。</p>
          </template>
        </div>
      </div>

      <!-- 决策复盘（观察口，默认折叠） -->
      <div class="card">
        <button class="w-full flex items-center justify-between text-sm font-medium px-1"
                @click="showReflections = !showReflections">
          <span>决策复盘 · 平仓后 AI 定性复盘（最近 {{ reflections.length }}）</span>
          <span class="text-xs text-[var(--text-muted)]">{{ showReflections ? '收起 ▲' : '展开 ▼' }}</span>
        </button>
        <div v-if="showReflections" class="mt-3 space-y-2">
          <div v-if="reflections.length === 0"
               class="text-xs text-[var(--text-muted)] px-1">暂无复盘记录（平仓后异步生成，含入场信号快照的仓位才有）。</div>
          <div v-for="(r, i) in reflections.slice().reverse()" :key="i"
               class="border border-[var(--border)] rounded-lg p-2.5">
            <div class="text-xs text-[var(--text-muted)] mb-1">
              <span class="font-mono">{{ r.coin }}</span> · {{ r.side }}
            </div>
            <div class="text-xs leading-5">{{ r.text }}</div>
          </div>
          <p class="text-[11px] text-[var(--text-muted)] px-1">
            复盘仅用于审阅决策质量，并已自动注入下次研究提示；不改变仓位、闸门或配置。</p>
        </div>
      </div>

      <!-- 评级汇总 -->
      <div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
        <div class="card"><div class="text-xs text-[var(--text-muted)]">采数缺口（门变盲）</div><div class="text-lg font-semibold mt-1 text-rose-400">{{ gapArms.length }}</div></div>
        <div class="card"><div class="text-xs text-[var(--text-muted)]">enforce 降级复核</div><div class="text-lg font-semibold mt-1 text-orange-400">{{ degradedArms.length }}</div></div>
        <div class="card"><div class="text-xs text-[var(--text-muted)]">建议复核（疑似误伤）</div><div class="text-lg font-semibold mt-1 text-amber-300">{{ reviewArms.length }}</div></div>
        <div class="card"><div class="text-xs text-[var(--text-muted)]">可考虑升 enforce</div><div class="text-lg font-semibold mt-1 text-emerald-400">{{ promoteArms.length }}</div></div>
        <div class="card"><div class="text-xs text-[var(--text-muted)]">enforce 维持</div><div class="text-lg font-semibold mt-1 text-emerald-300">{{ maintainArms.length }}</div></div>
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
              <th class="py-2 px-3 font-medium whitespace-nowrap"
                  title="仅统计命中且成熟、|反事实 pnl|≥$0.50 的实质性笔合计（滤除手续费/点差级小额噪声）。正=采纳该臂净亏钱（臂有害），负=采纳该臂净省钱（臂有益）；只写 pnl_pct、无金额维度的臂不适用">
                实质性反事实合计
              </th>
              <th class="py-2 px-3 font-medium">说明</th>
              <th class="py-2 px-3 font-medium text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="a in arms" :key="a.arm"
                class="border-b border-[var(--border)] last:border-0 hover:bg-[var(--surface-hover)]"
                :class="a.verdict === 'DATA_GAP' ? 'bg-rose-500/10'
                         : a.verdict === 'ENFORCE_DEGRADED_REVIEW' ? 'bg-orange-500/10' : ''">
              <td class="py-2.5 px-3 font-mono font-semibold whitespace-nowrap"
                  :title="ARM_TITLE[a.arm] || ''">
                {{ a.arm }}
                <span v-if="a.collection_stalled" class="text-amber-400" title="近 24h 0 条写入">⏸</span>
                <div v-if="armCN(a.arm)" class="font-sans font-normal text-[10px] text-[var(--text-muted)]">{{ armCN(a.arm) }}</div>
              </td>
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
                  <div class="text-[10px] text-[var(--text-muted)] font-mono"
                       :title="s.decision_scope === 'gate_layer'
         ? '仅统计真实下单闸门层（gate）的拦/放决策；观察条数含 prefilter 预筛流（仅记录拦截，不代表命中率）'
         : ''">
                    命中 {{ s.hits }}/{{ s.decisions }}（{{ hitRateTxt(s) }}）<span
                      v-if="s.decision_scope === 'gate_layer'"
                      class="text-sky-400/80">闸门层</span>
                  </div>
                  <div class="text-[10px] text-[var(--text-muted)] font-mono">回填 {{ s.mature_outcomes }}（胜{{ s.outcome_wins }}/负{{ s.outcome_losses }}）</div>
                  <!-- M6：短窗 outcome 成熟滞后，结论只采信最长窗 -->
                  <div v-if="s.outcomes_pending" class="text-[10px] text-amber-400/80 font-mono" title="短窗内尚无成熟 outcome（持仓未到结算龄）">
                    outcome 待回填
                  </div>
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
                <!-- M4：回填率；分母修正后的命中集有害率 -->
                <div class="font-mono text-[10px] mt-1" :class="Number(a.backfill_rate) < 0.2 ? 'text-amber-400/90' : 'text-[var(--text-muted)]'">
                  回填率 {{ backfillTxt(a) }}
                </div>
                <div v-if="a.hit_set_harmful_rate !== null && a.hit_set_harmful_rate !== undefined"
                     class="font-mono text-[10px] mt-0.5"
                     :class="Number(a.hit_set_harmful_rate) > 0.5 ? 'text-rose-400' : 'text-[var(--text-muted)]'"
                     :title="a.harmful_rate_basis === 'hit_set' ? '命中且成熟样本口径（分母修正）' : '命中集不足，回退全记录口径，可能低估真实误伤'">
                  命中集有害率 {{ hitHarmTxt(a) }}
                  <span v-if="a.harmful_rate_basis === 'all_records'" class="text-amber-400/80">⚠口径</span>
                </div>
              </td>
              <td class="py-2.5 px-3 whitespace-nowrap align-top">
                <template v-if="materialSumTxt(a)">
                  <div class="font-mono text-[11px]"
                       :class="Number(a.hit_set_material_pnl_sum) > 0 ? 'text-rose-400' : 'text-emerald-400'"
                       title="正=采纳该臂净亏钱（有害）；负=采纳该臂净省钱（有益）">
                    {{ materialSumTxt(a) }}
                  </div>
                </template>
                <div v-else-if="a.harmful_rate_money_basis"
                     class="font-mono text-[10px] text-[var(--text-muted)]">
                  无实质性笔
                </div>
                <span v-else class="text-[var(--text-muted)]"
                      title="成熟命中样本不足，或该臂只写 pnl_pct 无金额维度">—</span>
              </td>
              <td class="py-2.5 px-3 min-w-[240px] align-top">
                <div class="text-[var(--text-muted)]">{{ a.reason }}</div>
                <!-- M8：signal 臂人工判定通道 -->
                <div v-if="a.signal_harmful_rate_note" class="text-[11px] text-amber-300 mt-1">👁 {{ a.signal_harmful_rate_note }}</div>
                <!-- M2/M4/M12/M13 等侧信号告警 -->
                <!-- 去重：signal_harmful_rate_note 同文案已由上方 👁 专通道渲染，不再重复 ⚠ -->
                <div v-for="(w2, i) in ((a.warnings || []).filter((w: string) => w !== a.signal_harmful_rate_note))" :key="i" class="text-[11px] text-amber-400/90 mt-1">⚠ {{ w2 }}</div>
                <!-- M11：sizing_v2 成本上限 §8.1 六条件与臂 verdict 并列，不混入单一评级 -->
                <details v-if="a.sv2_cost" class="mt-1.5">
                  <summary class="text-[11px] cursor-pointer outline-none"
                           :class="a.sv2_cost.all_pass ? 'text-emerald-400' : 'text-amber-300'">
                    §8.1 成本闸门：{{ a.sv2_cost.gate === 'PROMOTE_CANDIDATE' ? '六条件全过' : '未全过·延长观察' }}
                    （n={{ a.sv2_cost.n }}）
                  </summary>
                  <div class="mt-1 space-y-0.5">
                    <div v-for="(c, ck) in a.sv2_cost.checks" :key="ck"
                         class="font-mono text-[10px] flex gap-1"
                         :title="c.detail">
                      <span :class="c.pass ? 'text-emerald-400' : 'text-amber-400'">{{ c.pass ? '✓' : '○' }}</span>
                      <span class="text-[var(--text-muted)]">{{ SV2_CHECK_CN[ck] || ck }}</span>
                    </div>
                    <div class="text-[10px] text-[var(--text-muted)] pt-0.5">{{ a.sv2_cost.gate_reason }}</div>
                  </div>
                </details>
              </td>
              <td class="py-2.5 px-3 text-right whitespace-nowrap align-top">
                <button v-if="a.verdict === 'PROMOTE_CANDIDATE'" class="btn btn-primary text-xs"
                        @click="router.push('/config')">去升级 →</button>
                <button v-else-if="a.verdict === 'REVIEW' || a.verdict === 'ENFORCE_DEGRADED_REVIEW'"
                        class="btn btn-ghost text-xs"
                        @click="router.push('/config')">去复核</button>
                <span v-else class="text-[var(--text-muted)]">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 历史回测证据（离线 K 线反事实回放聚合；与 live 评级相互印证） -->
      <div v-if="backfill" class="card overflow-x-auto">
        <div class="flex items-center justify-between flex-wrap gap-2 mb-1 px-1">
          <div class="text-sm font-medium flex items-center gap-2">
            历史回测证据（离线 K 线反事实回放）
            <span class="badge badge-muted">只读 · 证据</span>
          </div>
          <div class="text-[11px] text-[var(--text-muted)] font-mono">
            {{ backfillPresent.length }}/{{ backfillArms.length }} 臂有回填产物 · 生成于 {{ backfill.generated_at }}
          </div>
        </div>
        <p class="text-[11px] text-[var(--text-muted)] mb-2 px-1 leading-4">
          离线回填器用历史 K 线对各臂的拦截/信号做反事实回放（数据源 /data/*.backfill.jsonl，60s 缓存）；
          上方评级基于 live 采数，此处为历史证据，两者相互印证。本区块只读，不触发任何配置变更。
        </p>
        <table class="w-full text-xs">
          <thead>
            <tr class="text-left text-[var(--text-muted)] border-b border-[var(--border)]">
              <th class="py-2 px-3 font-medium whitespace-nowrap">风控臂</th>
              <th class="py-2 px-3 font-medium text-right">样本量</th>
              <th class="py-2 px-3 font-medium whitespace-nowrap">数据截至</th>
              <th class="py-2 px-3 font-medium text-right">胜率</th>
              <th class="py-2 px-3 font-medium text-right">平均盈亏</th>
              <th class="py-2 px-3 font-medium text-right">中位盈亏</th>
              <th class="py-2 px-3 font-medium">多空拆分（胜率/均值）</th>
              <th class="py-2 px-3 font-medium">臂特定证据</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="a in backfillArms" :key="a.arm"
                class="border-b border-[var(--border)] last:border-0 hover:bg-[var(--surface-hover)]"
                :class="a.present ? '' : 'opacity-50'">
              <td class="py-2.5 px-3 font-mono font-semibold whitespace-nowrap">{{ a.arm }}</td>
              <template v-if="a.present">
                <td class="py-2.5 px-3 font-mono text-right">{{ a.records }}</td>
                <td class="py-2.5 px-3 font-mono text-[11px] whitespace-nowrap">{{ isoDay(a.mtime) }}</td>
                <td class="py-2.5 px-3 font-mono text-right">{{ wrTxt(a.pnl?.win_rate) }}</td>
                <td class="py-2.5 px-3 font-mono text-right" :class="pctClass(a.pnl?.avg_pct)">{{ pctTxt(a.pnl?.avg_pct) }}</td>
                <td class="py-2.5 px-3 font-mono text-right" :class="pctClass(a.pnl?.median_pct)">{{ pctTxt(a.pnl?.median_pct) }}</td>
                <td class="py-2.5 px-3 font-mono text-[11px] whitespace-nowrap">
                  <span v-if="a.by_side?.long" class="mr-2">
                    多 <span :class="pctClass(a.by_side.long.avg_pct)">{{ wrTxt(a.by_side.long.win_rate) }}/{{ pctTxt(a.by_side.long.avg_pct) }}</span>
                  </span>
                  <span v-if="a.by_side?.short">
                    空 <span :class="pctClass(a.by_side.short.avg_pct)">{{ wrTxt(a.by_side.short.win_rate) }}/{{ pctTxt(a.by_side.short.avg_pct) }}</span>
                  </span>
                  <span v-if="!a.by_side" class="text-[var(--text-muted)]">—</span>
                </td>
                <td class="py-2.5 px-3 text-[11px] leading-4">
                  <div v-for="(line, i) in extrasTxt(a)" :key="i" class="font-mono">{{ line }}</div>
                  <span v-if="!extrasTxt(a).length" class="text-[var(--text-muted)]">—</span>
                </td>
              </template>
              <td v-else colspan="7" class="py-2.5 px-3 text-[var(--text-muted)]">
                无回填产物（live 样本不足，继续采数）
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 长周期信号再生回放（regen_param_sweep：三臂反事实拦截网格扫描，walk-forward 三段） -->
      <div v-if="regen" class="card overflow-x-auto">
        <div class="flex items-center justify-between flex-wrap gap-2 mb-1 px-1">
          <div class="text-sm font-medium flex items-center gap-2">
            长周期信号再生回放（{{ regen.days ?? '—' }} 天 · walk-forward）
            <span class="badge badge-muted">只读 · 证据</span>
          </div>
          <div v-if="regenPresent" class="text-[11px] text-[var(--text-muted)] font-mono">
            最新回测生成于 {{ regen.generated_at }} · 60s 缓存，重跑后自动更新
          </div>
        </div>
        <p class="text-[11px] text-[var(--text-muted)] mb-2 px-1 leading-4">
          用历史 K 线重新生成技术信号，回放三臂（ta_late_entry / trend_filter_200ma / daily_extension_cap）
          的反事实拦截：train/val/test 三段前推，EV 为每笔净期望（含 5bps 费用）；
          「拦截受益/笔」为正代表拦掉的尽是亏钱单（臂有益），为负代表误伤。本区块只读，不改任何配置。
        </p>

        <div v-if="!regenPresent" class="text-[var(--text-muted)] text-xs px-1 py-2">
          {{ regen.note || '暂无回放报告' }}
        </div>

        <template v-else>
          <!-- 概览统计 -->
          <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
            <div class="rounded-lg border border-[var(--border)] p-3">
              <div class="text-xs text-[var(--text-muted)]">回放窗口</div>
              <div class="text-sm font-semibold mt-1 font-mono">
                {{ fmtWinMs(regenWindow.t_min) }} ~ {{ fmtWinMs(regenWindow.t_max) }}
              </div>
              <div class="text-[11px] mt-1 text-[var(--text-muted)]">
                train &lt; {{ fmtWinMs(regenWindow.train_end) }} · val &lt; {{ fmtWinMs(regenWindow.val_end) }} · test 之后
              </div>
            </div>
            <div class="rounded-lg border border-[var(--border)] p-3">
              <div class="text-xs text-[var(--text-muted)]">再生候选信号</div>
              <div class="text-xl font-semibold mt-1 font-mono">{{ regen.n_candidates ?? '—' }}</div>
              <div class="text-[11px] mt-1 text-[var(--text-muted)]">
                做多 72h 窗 {{ regen.n_candidates_long72 ?? '—' }} · 持有 {{ regen.hold_bars ?? '—' }} 棒
              </div>
            </div>
            <div class="rounded-lg border border-[var(--border)] p-3">
              <div class="text-xs text-[var(--text-muted)]">覆盖币数</div>
              <div class="text-xl font-semibold mt-1 font-mono">{{ regen.n_coins ?? '—' }}</div>
              <div class="text-[11px] mt-1 text-[var(--text-muted)]">报告文件 {{ isoDay(regen.mtime) }} 更新</div>
            </div>
            <div class="rounded-lg border border-[var(--border)] p-3">
              <div class="text-xs text-[var(--text-muted)]">与实盘拦截一致率</div>
              <div class="text-xl font-semibold mt-1 font-mono"
                   :class="Number(regenOverlap.block_agree_rate) >= 0.99 ? 'text-emerald-400' : 'text-amber-300'">
                {{ agreeTxt(regenOverlap.block_agree_rate) }}
              </div>
              <div class="text-[11px] mt-1 text-[var(--text-muted)]">
                对照 {{ regenOverlap.block_compared ?? 0 }}/{{ regenOverlap.live_rows ?? 0 }} 行
              </div>
            </div>
          </div>

          <!-- overlap 细节：回放指标重算 vs 实盘记录（证明回放忠实复现实盘闸门） -->
          <div class="text-[11px] text-[var(--text-muted)] mb-3 px-1 font-mono leading-5">
            指标重算偏差（MAE）：
            <span v-for="(d, k) in (regenOverlap.indicator_diff || {})" :key="k" class="mr-3">
              {{ k }} {{ Number(d?.mae ?? 0).toFixed(5) }}
            </span>
            —— 与实盘闸门判定一致率越高，回放结论越可信。
          </div>

          <!-- 参数平台探测：三轴曲线 + plateau picks -->
          <div v-if="axisRows.length" class="mb-4">
            <div class="text-xs font-medium mb-2 px-1 text-[var(--text-muted)]">
              参数平台探测（固定其他参数，单轴扫描净 EV；三段符号一致才算稳定平台）
            </div>
            <div class="grid lg:grid-cols-3 gap-3">
              <div v-for="a in axisRows" :key="a.axis" class="rounded-lg border border-[var(--border)] p-3">
                <div class="flex items-center justify-between gap-2 mb-1.5">
                  <div class="text-xs font-medium">
                    {{ a.cn }} <span class="font-mono text-[var(--text-muted)]">{{ a.axis }}</span>
                  </div>
                  <span v-if="a.pick !== null && a.pick !== undefined" class="badge badge-ok">平台值 {{ a.pick }}</span>
                  <span v-else class="badge badge-muted" title="三段 EV 未出现符号一致的稳定平台，维持现值">无稳定平台</span>
                </div>
                <div class="text-[11px] text-[var(--text-muted)] mb-1">现值 <span class="font-mono">{{ a.baseline ?? '—' }}</span></div>
                <table class="w-full text-[11px] font-mono">
                  <thead>
                    <tr class="text-left text-[var(--text-muted)] border-b border-[var(--border)]">
                      <th class="py-1 pr-2 font-medium">取值</th>
                      <th class="py-1 pr-2 font-medium text-right">n</th>
                      <th class="py-1 pr-2 font-medium text-right">train</th>
                      <th class="py-1 pr-2 font-medium text-right">val</th>
                      <th class="py-1 font-medium text-right">test</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="p in a.points" :key="p.axis"
                        class="border-b border-[var(--border)] last:border-0"
                        :class="p.axis === a.baseline ? 'text-sky-300' : ''"
                        :title="p.sign_consistent ? '三段 EV 符号一致' : '三段 EV 符号不一致'">
                      <td class="py-1 pr-2">
                        {{ p.axis }}
                        <span v-if="p.axis === a.baseline" title="当前现值">●</span>
                        <span v-if="p.sign_consistent" class="text-emerald-400" title="三段符号一致">✓</span>
                      </td>
                      <td class="py-1 pr-2 text-right">{{ p.n }}</td>
                      <td class="py-1 pr-2 text-right" :class="pctClass(p.evs?.train)">{{ usdTxt(p.evs?.train) }}</td>
                      <td class="py-1 pr-2 text-right" :class="pctClass(p.evs?.val)">{{ usdTxt(p.evs?.val) }}</td>
                      <td class="py-1 text-right" :class="pctClass(p.evs?.test)">{{ usdTxt(p.evs?.test) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <!-- 三臂 walk-forward 扫描表（统一结构） -->
          <div v-for="t in sweepTables" :key="t.key" class="mb-4">
            <div class="text-xs font-medium mb-1.5 px-1 text-[var(--text-muted)]">{{ t.title }}</div>
            <table class="w-full text-xs">
              <thead>
                <tr class="text-left text-[var(--text-muted)] border-b border-[var(--border)]">
                  <th class="py-1.5 px-3 font-medium">参数</th>
                  <th class="py-1.5 px-3 font-medium text-right">train 受益/笔</th>
                  <th class="py-1.5 px-3 font-medium text-right">val 受益/笔</th>
                  <th class="py-1.5 px-3 font-medium text-right">test 受益/笔</th>
                  <th class="py-1.5 px-3 font-medium text-right">val 拦截 n</th>
                  <th class="py-1.5 px-3 font-medium text-right">val 拦截胜率</th>
                  <th class="py-1.5 px-3 font-medium text-right">val 拦截 EV</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(r, i) in t.rows" :key="i"
                    class="border-b border-[var(--border)] last:border-0 hover:bg-[var(--surface-hover)]">
                  <td class="py-1.5 px-3 font-mono text-[11px] whitespace-nowrap">{{ t.label(r) }}</td>
                  <td class="py-1.5 px-3 font-mono text-right" :class="avoidedClass(r.train)">{{ avoidedTxt(r.train) }}</td>
                  <td class="py-1.5 px-3 font-mono text-right" :class="avoidedClass(r.val)">{{ avoidedTxt(r.val) }}</td>
                  <td class="py-1.5 px-3 font-mono text-right" :class="avoidedClass(r.test)">{{ avoidedTxt(r.test) }}</td>
                  <td class="py-1.5 px-3 font-mono text-right">{{ r.val?.blocked?.n ?? '—' }}</td>
                  <td class="py-1.5 px-3 font-mono text-right">{{ wrTxt(r.val?.blocked?.wr) }}</td>
                  <td class="py-1.5 px-3 font-mono text-right" :class="pctClass(r.val?.blocked?.ev)">{{ usdTxt(r.val?.blocked?.ev) }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- 手动触发回放（operator 门控；只重跑离线脚本并改写报告文件，不触交易链路） -->
          <div v-if="canRefresh" class="mt-2 rounded-lg border border-[var(--border)] p-3">
            <div class="flex items-center flex-wrap gap-2">
              <span class="text-xs font-medium">手动回放</span>
              <label class="text-[11px] text-[var(--text-muted)]">
                天数
                <input v-model.number="regenDays" type="number" min="30" max="365" step="10"
                       class="ml-1 w-20 bg-[var(--surface)] border border-[var(--border)] rounded px-2 py-1 text-xs font-mono"
                       :disabled="regenRunning || regenTriggering" />
              </label>
              <label class="text-[11px] text-[var(--text-muted)]">
                币圈
                <input v-model.trim="regenCoins" type="text" placeholder="留空=默认币圈"
                       class="ml-1 w-44 bg-[var(--surface)] border border-[var(--border)] rounded px-2 py-1 text-xs font-mono"
                       :disabled="regenRunning || regenTriggering" />
              </label>
              <button class="btn btn-primary text-xs" :disabled="regenRunning || regenTriggering"
                      @click="triggerRegen">
                {{ regenTriggering ? '触发中...' : regenRunning ? '回放运行中...' : '▶ 触发回放' }}
              </button>
              <span v-if="regenRunning" class="text-[11px] text-amber-300 font-mono">
                ⏳ {{ regenStatus?.days ?? regenDays }} 天回放进行中（{{ isoDay(regenStatus?.started_at) }}
                {{ String(regenStatus?.started_at || '').slice(11, 19) }} 起跑），每 5s 轮询，完成自动刷新
              </span>
              <span v-else-if="regenStatus?.exit_code !== null && regenStatus?.exit_code !== undefined"
                    class="text-[11px] font-mono"
                    :class="regenStatus.exit_code === 0 ? 'text-emerald-400' : 'text-rose-400'">
                上次回放 {{ regenStatus.exit_code === 0 ? '成功' : `失败(exit ${regenStatus.exit_code})` }}
                · {{ regenStatus.days }} 天 · {{ String(regenStatus.finished_at || '').slice(0, 19).replace('T', ' ') }}
              </span>
            </div>
            <p class="text-[11px] text-[var(--text-muted)] mt-1.5 leading-4">
              触发后在后台重跑 regen_param_sweep.py --write 并改写报告文件，本卡 60s 缓存被主动失效、完成即见最新回测时间；
              同一时间只允许一个回放任务（重复触发返回 409）。纯离线计算，不改配置/闸门/下单。
            </p>
          </div>
        </template>
      </div>

      <!-- 每晚评级历史 + M5 趋势图 -->
      <div v-if="history.length" class="card">
        <div class="text-sm font-medium mb-3 px-1">
          评级趋势（近 30 天，{{ history.length }} 个每晚快照）
        </div>
        <div class="grid lg:grid-cols-2 gap-4">
          <div>
            <div class="text-xs text-[var(--text-muted)] mb-1 px-1">每晚评级分布（条数堆叠）</div>
            <v-chart :option="verdictOption" class="h-64 w-full" autoresize />
          </div>
          <div>
            <div class="text-xs text-[var(--text-muted)] mb-1 px-1">各臂命中率走势（当前 enforce/shadow 臂）</div>
            <v-chart :option="hitRateOption" class="h-64 w-full" autoresize />
          </div>
        </div>
      </div>

      <div class="card overflow-x-auto">
        <div class="text-sm font-medium mb-2 px-1">
          每晚评级历史明细（近 30 天，{{ history.length }} 个 cron 快照；手动重评不入趋势）
        </div>
        <table class="w-full text-xs">
          <thead>
            <tr class="text-left text-[var(--text-muted)] border-b border-[var(--border)]">
              <th class="py-2 px-3 font-medium">日期</th>
              <th class="py-2 px-3 font-medium text-right">真实成交</th>
              <th class="py-2 px-3 font-medium text-right">缺口</th>
              <th class="py-2 px-3 font-medium text-right">enforce降级</th>
              <th class="py-2 px-3 font-medium text-right">复核</th>
              <th class="py-2 px-3 font-medium text-right">可升级</th>
              <th class="py-2 px-3 font-medium text-right">enforce维持</th>
              <th class="py-2 px-3 font-medium text-right">采集中</th>
              <th class="py-2 px-3 font-medium text-right">关闭</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(s, i) in recentHistory" :key="i" class="border-b border-[var(--border)] last:border-0 hover:bg-[var(--surface-hover)]">
              <td class="py-2 px-3 font-mono whitespace-nowrap">{{ fmtDay(s.ts) }}</td>
              <td class="py-2 px-3 font-mono text-right">{{ s.real_closes ?? 0 }}</td>
              <td class="py-2 px-3 font-mono text-right text-rose-400">{{ snapshotCounts(s).DATA_GAP || 0 }}</td>
              <td class="py-2 px-3 font-mono text-right text-orange-400">{{ snapshotCounts(s).ENFORCE_DEGRADED_REVIEW || 0 }}</td>
              <td class="py-2 px-3 font-mono text-right text-amber-300">{{ snapshotCounts(s).REVIEW || 0 }}</td>
              <td class="py-2 px-3 font-mono text-right text-emerald-400">{{ snapshotCounts(s).PROMOTE_CANDIDATE || 0 }}</td>
              <td class="py-2 px-3 font-mono text-right text-emerald-300">{{ snapshotCounts(s).ENFORCE_MAINTAIN || 0 }}</td>
              <td class="py-2 px-3 font-mono text-right">{{ (snapshotCounts(s).INSUFFICIENT_DATA || 0) + (snapshotCounts(s).COLLECTING || 0) }}</td>
              <td class="py-2 px-3 font-mono text-right text-[var(--text-muted)]">{{ snapshotCounts(s).OFF || 0 }}</td>
            </tr>
            <tr v-if="recentHistory.length === 0">
              <td colspan="9" class="py-8 text-center text-[var(--text-muted)]">
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
