<script setup lang="ts">
/**
 * 系统配置
 * - 从 hermes-trader 拉取配置 schema（类型 + 默认值）与当前配置
 * - 按业务分组渲染表单，参数名中文化，附带英文 key 和说明
 * - 保存到 /api/dashboard/config，并提供备份/回滚/历史入口
 */
import { computed, onMounted, reactive, ref } from 'vue';
import http from '@/shared/api/client';
import { useToast } from '@/stores/toast';

const toast = useToast();
const schema = ref<Record<string, { type: string; default: any }>>({});
const config = reactive<Record<string, any>>({});
const history = ref<any[]>([]);
const snapshots = ref<any[]>([]);
const loading = ref(true);
const saving = ref(false);
const snapshotting = ref(false);
const rawMode = ref(false);
const rawText = ref('');

// ---- 中文标签与说明 ----
interface ParamMeta {
  label: string;
  hint?: string;
  unit?: string;
}

const PARAM_META: Record<string, ParamMeta> = {
  // 运行模式
  mode: { label: '运行模式', hint: 'LIVE=实盘运行（允许开仓）/ OFF=暂停（停止开仓）/ SHADOW=影子（只记录不执行）' },
  override_requires_ai: { label: 'AI 二次研判', hint: '人工干预信号是否需要 AI 再次研判确认' },
  enable_crypto: { label: '加密货币交易', hint: '是否启用加密货币交易对' },
  enable_hip3: { label: 'HIP-3 市场', hint: '是否启用 HIP-3 去中心化市场' },
  trend_surface_enabled: { label: '趋势面分析', hint: '启用趋势面因子辅助决策' },

  // 仓位与杠杆
  equity_fraction_per_trade: { label: '单笔权益占比', unit: '%', hint: '每笔交易占用总权益的百分比' },
  leverage: { label: '杠杆倍数', unit: 'x', hint: '默认杠杆倍数' },
  max_trade_notional_usd: { label: '单笔最大名义金额', unit: ' USDC' },
  max_concurrent: { label: '最大并发持仓数', unit: ' 个' },
  max_total_notional_pct: { label: '总名义金额上限', unit: '%', hint: '所有持仓合计名义金额占权益的最大比例' },
  tp_scale_fraction: { label: '止盈比例', unit: '%', hint: '止盈时平仓的仓位比例' },
  conviction_sizing: { label: '置信度仓位', hint: '根据 AI 置信度动态调整仓位大小' },

  // 风控
  max_daily_loss_usd: { label: '单日最大亏损', unit: ' USDC', hint: '当日亏损超过此值则停止交易（负数表示亏损额）' },
  daily_giveback_halt_pct: { label: '盈利回吐暂停比例', unit: '%', hint: '当日盈利回吐达到此比例时暂停' },
  daily_giveback_min_peak_usd: { label: '回吐触发峰值', unit: ' USDC', hint: '当日盈利达到此金额后才启用回吐保护' },
  min_available_margin_pct: { label: '最低可用保证金', unit: '%' },
  cooldown_min: { label: '常规冷却时间', unit: ' 分钟' },
  loss_cooldown_min: { label: '亏损后冷却时间', unit: ' 分钟' },
  research_cooldown_min: { label: '研判冷却时间', unit: ' 分钟', hint: '同一币种两次 AI 研判的最小间隔' },
  held_research_interval_min: { label: '持仓研判刷新间隔', unit: ' 分钟', hint: '持仓期间重新研判的时间间隔' },
  min_ai_close_hold_min: { label: 'AI 平仓最短持仓', unit: ' 分钟', hint: 'AI 信号建议平仓前的最短持仓时间' },
  sl_atr_mult: { label: 'ATR 止损倍数', unit: 'x', hint: '止损距离 = ATR × 此倍数' },
  min_ai_confidence: { label: '最低 AI 置信度', unit: '%', hint: '低于此置信度的信号将被跳过（0-1 小数）' },
  max_atr_pct: { label: '最大 ATR 波动', unit: '%', hint: '入场前 ATR 占价超过此值则拦截' },
  max_spread_pct: { label: '最大点差', unit: '%', hint: '买卖价差超过此值则拦截' },
  spread_gate_fail_open: { label: '点差门放行', hint: '点差检查异常时是否放行而非拦截' },

  // HIP-3
  hip3_dex_allowlist: { label: 'DEX 白名单', hint: '仅交易这些 DEX 的市场（每行一个）' },
  hip3_dex_blocklist: { label: 'DEX 黑名单', hint: '排除这些 DEX 的市场' },
  min_hip3_volume_usd: { label: 'HIP-3 最小成交量', unit: ' USDC' },

  // 市场过滤
  coin_allowlist: { label: '币种白名单', hint: '仅交易这些币种（每行一个）' },
  coin_blocklist: { label: '币种黑名单', hint: '排除这些币种' },
  min_market_volume_usd: { label: '最小市场成交量', unit: ' USDC' },
  min_short_volume_usd: { label: '做空最小成交量', unit: ' USDC' },
  max_crypto_long_correlated: { label: '最大多头相关性数量', unit: ' 个', hint: '高度相关多头持仓数量上限' },
  crowded_with_min_conf: { label: '拥挤度最低置信度', unit: '（0-1）', hint: '判定为拥挤交易所需的最低置信度' },
  counter_regime_min_conf: { label: '逆势最低置信度', unit: '（0-1）', hint: '逆趋势交易所需的最低置信度' },
  min_trend_score: { label: '最低趋势评分', hint: '趋势市所需的最低趋势分数（0-1）' },
  chop_min_conf: { label: '震荡市最低置信度', unit: '（0-1）', hint: '判定为震荡市所需的最低置信度' },
  chop_min_score: { label: '震荡市最低评分', hint: '判定为震荡市所需的最低分数（0-100）' },
  against_funding_min_conf: { label: '逆资金费率最低置信度', unit: '（0-1）', hint: '逆资金费率交易所需的最低置信度' },
  against_funding_min_score: { label: '逆资金费率最低评分', hint: '逆资金费率交易所需的最低分数（0-100）' },
  strong_trend_threshold: { label: '强趋势阈值', unit: '（0-1）', hint: '趋势强度超过此值视为强趋势' },
  trend_threshold: { label: '趋势阈值', unit: '（0-1）', hint: '趋势强度超过此值视为趋势市' },
  neutral_threshold: { label: '中性阈值', unit: '（0-1）', hint: '趋势强度低于此值视为中性市' },

  // 信号强化
  signal_enforcement: { label: '信号强制执行', hint: '信号否决/增强/鲸鱼信号阈值（JSON 对象）' },
  shadow_signals: { label: '影子信号', hint: '记录但不执行信号（GEX/做空量/鲸鱼/新闻，JSON 对象）' },
  gex_signal: { label: 'GEX 信号', hint: 'Gamma Exposure 信号配置（JSON 对象）' },
  momentum_continuation: { label: '动量延续', hint: '趋势中继回调入场因子（JSON 对象）' },
  candlestick_patterns: { label: 'K线形态', hint: 'K线形态识别参数（JSON 对象）' },
  momentum_reentry: { label: '动量回补', hint: '趋势回归重新入场（JSON 对象）' },
  runner_mover_surface: { label: '涨幅榜扫描', hint: 'Runner/Mover 涨幅榜筛选阈值（JSON 对象）' },
  capital_rotation: { label: '资金轮动', hint: '弱势仓位换强势标的（JSON 对象）' },
  research_rescore_delta: { label: '研判重评分增量', hint: '持仓评分变化超过此值时豁免研判冷却（0=不启用）' },
  whale_scan_bypass: { label: '鲸鱼扫描绕过', hint: '鲸鱼信号绕过趋势检查' },
  whale_regime_bypass: { label: '鲸鱼趋势绕过', hint: '鲸鱼级信号可绕过常规趋势检查' },
  whale_force_execute: { label: '鲸鱼强制执行', hint: '鲸鱼级信号触发强制执行' },
  whale_size_multiplier: { label: '鲸鱼规模倍数', unit: 'x', hint: '鲸鱼信号的仓位放大倍数' },
  block_counter_trend_bypass: { label: '拦截逆势绕过', hint: '阻止逆势交易绕过风控' },
  force_execute_composite: { label: '综合强制分值', hint: '综合评分超过此值则强制执行' },
  composite_force_execute: { label: '综合强制执行开关', hint: '是否启用综合分值强制执行' },
  breakout_force_execute: { label: '突破强制执行', hint: '突破信号触发强制执行' },
  force_execute_slow_burn_count: { label: '慢燃强制执行次数', unit: ' 次', hint: '连续触发次数达到此值则强制执行' },
  ta_sidestep_force_execute: { label: 'TA 回避强制执行', hint: 'TA 回避信号达到阈值时强制执行' },
  ta_sidestep_min_slow_burn_count: { label: 'TA 慢燃阈值', unit: ' 次', hint: 'TA 回避慢燃信号次数阈值' },

  // 嵌套对象（JSON 编辑）
  runner_entry_gate: { label: '入场闸门', hint: 'Runner 开仓前的门槛配置（置信度/综合分/做空限制，JSON 对象）' },
  plan_b: { label: 'B 计划', hint: 'RSI 极端区间的备用仓位策略（JSON 对象）' },
  atr_risk_sizing: { label: 'ATR 风险仓位', hint: '基于 ATR 止损的动态仓位计算（JSON 对象）' },
  regime_classifier: { label: '趋势分类器', hint: 'EMA/ADX 参数，用于判定趋势/震荡市（JSON 对象）' },
  debate_gate: { label: '辩论闸门', hint: '多智能体辩论一致通过门槛（JSON 对象）' },

  // DSL 退出
  dsl_exit: { label: 'DSL 退出策略', hint: 'DSL 仓位的退出规则组（JSON 对象）' },

  // 未在 PARAM_META 中显式标注但 trader schema 中存在的键，
  // 会自动归入「其他参数」分组并以 key 作为标签显示。
};

// DSL 退出子参数
const DSL_META: Record<string, ParamMeta> = {
  max_loss_pct: { label: '最大亏损比例', unit: '%' },
  max_loss_roe_pct: { label: '最大 ROE 亏损', unit: '%' },
  protect_pct: { label: '保护阈值', unit: '%' },
  retrace_threshold: { label: '回撤阈值', unit: '%' },
  hard_timeout_minutes: { label: '硬超时', unit: ' 分钟' },
  stale_flat_timeout_minutes: { label: '停滞平仓超时', unit: ' 分钟' },
  breakeven_trigger_pct: { label: '保本触发', unit: '%' },
  breakeven_lock_pct: { label: '保本锁定', unit: '%' },
};

interface Section {
  title: string;
  icon: string;
  keys: string[];
}

const SECTIONS: Section[] = [
  { title: '运行模式', icon: '⚡', keys: ['mode', 'override_requires_ai', 'enable_crypto', 'enable_hip3', 'trend_surface_enabled'] },
  { title: '仓位与杠杆', icon: '⚖️', keys: ['equity_fraction_per_trade', 'leverage', 'max_trade_notional_usd', 'max_concurrent', 'max_total_notional_pct', 'tp_scale_fraction', 'conviction_sizing'] },
  { title: '风控参数', icon: '🛡️', keys: ['max_daily_loss_usd', 'daily_giveback_halt_pct', 'daily_giveback_min_peak_usd', 'min_available_margin_pct', 'cooldown_min', 'loss_cooldown_min', 'research_cooldown_min', 'held_research_interval_min', 'min_ai_close_hold_min', 'sl_atr_mult', 'min_ai_confidence', 'max_atr_pct', 'max_spread_pct', 'spread_gate_fail_open'] },
  { title: 'HIP-3 市场', icon: '◆', keys: ['hip3_dex_allowlist', 'hip3_dex_blocklist', 'min_hip3_volume_usd'] },
  { title: '市场过滤', icon: '◎', keys: ['coin_allowlist', 'coin_blocklist', 'min_market_volume_usd', 'min_short_volume_usd', 'max_crypto_long_correlated', 'crowded_with_min_conf', 'counter_regime_min_conf', 'min_trend_score', 'chop_min_conf', 'chop_min_score', 'against_funding_min_conf', 'against_funding_min_score', 'strong_trend_threshold', 'trend_threshold', 'neutral_threshold'] },
  { title: '信号强化', icon: '📡', keys: ['signal_enforcement', 'shadow_signals', 'gex_signal', 'momentum_continuation', 'candlestick_patterns', 'momentum_reentry', 'runner_mover_surface', 'capital_rotation', 'research_rescore_delta', 'whale_scan_bypass', 'whale_regime_bypass', 'whale_force_execute', 'whale_size_multiplier', 'block_counter_trend_bypass', 'force_execute_composite', 'composite_force_execute', 'breakout_force_execute', 'force_execute_slow_burn_count', 'ta_sidestep_force_execute', 'ta_sidestep_min_slow_burn_count'] },
  { title: '高级策略', icon: '🧩', keys: ['runner_entry_gate', 'plan_b', 'atr_risk_sizing', 'regime_classifier', 'debate_gate', 'debate_research'] },
];

// 将 schema keys 按 SECTIONS 分组，剩余归入"其他"
const grouped = computed(() => {
  const allKeys = Object.keys(schema.value);
  const assigned = new Set<string>();
  const result: { section: Section; keys: string[] }[] = [];
  for (const s of SECTIONS) {
    const keys = s.keys.filter((k) => allKeys.includes(k));
    keys.forEach((k) => assigned.add(k));
    if (keys.length) result.push({ section: s, keys });
  }
  // DSL 退出单独处理
  if (allKeys.includes('dsl_exit')) assigned.add('dsl_exit');
  const remaining = allKeys.filter((k) => !assigned.has(k));
  if (remaining.length) {
    result.push({ section: { title: '其他参数', icon: '⚙️', keys: remaining }, keys: remaining });
  }
  return result;
});

const hasDslExit = computed(() => Object.keys(schema.value).includes('dsl_exit'));
const dslExit = computed(() => (config.dsl_exit ?? {}) as Record<string, any>);

function meta(key: string): ParamMeta {
  return PARAM_META[key] || { label: key };
}

function modeLabel(v: any): string {
  if (v === 'LIVE' || v === 'live') return '实盘';
  if (v === 'OFF' || v === 'off') return '暂停';
  if (v === 'SHADOW' || v === 'shadow') return '影子';
  return String(v ?? '—');
}

async function load() {
  loading.value = true;
  try {
    const [s, c] = await Promise.all([
      http.get('/api/portal/trader/api/dashboard/config/schema'),
      http.get('/api/portal/trader/api/dashboard/config'),
    ]);
    schema.value = s.data;
    Object.keys(config).forEach((k) => delete config[k]);
    Object.assign(config, c.data);
    rawText.value = JSON.stringify(config, null, 2);
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '加载配置失败');
  } finally {
    loading.value = false;
  }
}

async function save() {
  saving.value = true;
  try {
    let payload = config;
    if (rawMode.value) {
      try {
        payload = JSON.parse(rawText.value);
      } catch {
        toast.err('JSON 格式错误');
        saving.value = false;
        return;
      }
    }
    for (const [k, m] of Object.entries(schema.value)) {
      if (payload[k] !== undefined) {
        if (m.type === 'int') payload[k] = parseInt(payload[k], 10);
        else if (m.type === 'float') payload[k] = parseFloat(payload[k]);
      }
    }
    // 只发送 schema 中已知的 key，过滤掉遗留/未知字段
    const knownKeys = new Set(Object.keys(schema.value));
    const filtered: Record<string, any> = {};
    for (const [k, v] of Object.entries(payload)) {
      if (knownKeys.has(k)) filtered[k] = v;
    }
    await http.post('/api/portal/trader/api/dashboard/config', { updates: filtered });
    toast.ok('配置已保存并即时生效');
    await load();
    await loadHistory();
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '保存失败');
  } finally {
    saving.value = false;
  }
}

async function loadHistory() {
  try {
    const { data } = await http.get('/api/portal/trader/api/dashboard/config/history');
    history.value = Array.isArray(data) ? data : data?.history || [];
    snapshots.value = data?.snapshots || [];
  } catch { /* 可选 */ }
}

async function createSnapshot() {
  if (snapshotting.value) return;
  snapshotting.value = true;
  try {
    const reason = window.prompt('备份备注（可选）：', '手动备份') || 'manual';
    const { data } = await http.post('/api/portal/trader/api/dashboard/config/backup', { reason });
    if (data?.snapshot) {
      toast.ok(`备份已创建 (${data.snapshot.id})`);
    } else {
      toast.ok('备份已创建');
    }
    await loadHistory();
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '备份失败');
  } finally {
    snapshotting.value = false;
  }
}

async function rollback(id?: string) {
  const label = id ? `快照 ${id}` : '上一次自动备份';
  if (!confirm(`确定回滚到${label}？当前配置将被覆盖。`)) return;
  try {
    await http.post('/api/portal/trader/api/dashboard/config/rollback', id ? { id } : {});
    toast.ok('已回滚');
    await load();
    await loadHistory();
  } catch (e: any) {
    toast.err(e?.response?.data?.detail || '回滚失败');
  }
}

function formatTs(ts: any): string {
  if (!ts) return '';
  // history events use ms; snapshots use unix seconds
  const n = typeof ts === 'number' ? ts : Number(ts);
  const ms = n > 1e12 ? n : n * 1000;
  const d = new Date(ms);
  const pad = (x: number) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function eventLabel(e: any): string {
  switch (e.event) {
    case 'config_update':
      return `配置变更: ${Object.keys(e.updates || {}).join(', ')}`;
    case 'config_rollback':
      return `回滚 (来源: ${e.from || 'backup'})`;
    case 'config_snapshot':
      return `手动备份 (${e.snapshot_id || ''})`;
    default:
      return '配置事件';
  }
}

onMounted(() => {
  load();
  loadHistory();
});
</script>

<template>
  <div class="space-y-5">
    <header class="flex items-center justify-between flex-wrap gap-3">
      <div>
        <h2 class="text-xl font-semibold">系统配置</h2>
        <p class="text-sm text-[var(--text-muted)] mt-1">交易引擎参数，保存后即时生效，每次变更自动备份</p>
      </div>
      <div class="flex gap-2 items-center">
        <label class="flex items-center gap-2 text-sm cursor-pointer">
          <input type="checkbox" v-model="rawMode" /> JSON 编辑
        </label>
        <button class="btn" :disabled="snapshotting" @click="createSnapshot">📸 手动备份</button>
        <button class="btn" @click="load">🔄 刷新</button>
        <button class="btn btn-primary" :disabled="saving" @click="save">💾 保存</button>
      </div>
    </header>

    <div v-if="loading" class="card text-center py-10 text-[var(--text-muted)]">加载中...</div>

    <template v-else>
      <!-- 原始 JSON 模式 -->
      <div v-if="rawMode" class="card">
        <textarea class="input w-full font-mono text-xs h-96" v-model="rawText"></textarea>
      </div>

      <!-- 分组表单模式 -->
      <div v-else class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        <div v-for="g in grouped" :key="g.section.title" class="card">
          <div class="flex items-center gap-2 mb-3 pb-2 border-b border-[var(--border)]">
            <span>{{ g.section.icon }}</span>
            <span class="font-semibold">{{ g.section.title }}</span>
            <span class="ml-auto text-[10px] text-[var(--text-muted)]">{{ g.keys.length }} 项</span>
          </div>
          <div class="space-y-3">
            <div v-for="key in g.keys" :key="key">
              <label class="flex items-center gap-1.5 text-sm">
                <span>{{ meta(key).label }}</span>
                <span class="text-[10px] text-[var(--text-muted)] font-mono">{{ key }}</span>
              </label>
              <p v-if="meta(key).hint" class="text-[11px] text-[var(--text-muted)] mt-0.5 leading-relaxed">{{ meta(key).hint }}</p>

              <!-- 布尔 -->
              <label v-if="schema[key].type === 'bool'" class="flex items-center gap-2 mt-1.5 cursor-pointer">
                <input type="checkbox" v-model="config[key]" />
                <span class="text-sm" :class="config[key] ? 'text-emerald-400' : 'text-[var(--text-muted)]'">{{ config[key] ? '已启用' : '已关闭' }}</span>
              </label>

              <!-- mode 特殊：下拉 -->
              <select v-else-if="key === 'mode'" class="input w-full mt-1" v-model="config[key]">
                <option value="LIVE">实盘运行 (LIVE)</option>
                <option value="OFF">暂停 (OFF)</option>
                <option value="SHADOW">影子模式 (SHADOW)</option>
              </select>

              <!-- 数值 -->
              <div v-else-if="schema[key].type === 'int' || schema[key].type === 'float'" class="flex items-center gap-2 mt-1">
                <input
                  type="number"
                  :step="schema[key].type === 'float' ? '0.01' : '1'"
                  class="input flex-1"
                  v-model.number="config[key]"
                />
                <span v-if="meta(key).unit" class="text-xs text-[var(--text-muted)] whitespace-nowrap">{{ meta(key).unit }}</span>
              </div>

              <!-- 列表 -->
              <textarea
                v-else-if="schema[key].type === 'list'"
                class="input w-full font-mono text-xs h-20 mt-1"
                :value="Array.isArray(config[key]) ? config[key].join('\n') : ''"
                @input="(e) => config[key] = (e.target as HTMLTextAreaElement).value.split('\n').map(s => s.trim()).filter(Boolean)"
              ></textarea>

              <!-- 对象 -->
              <textarea
                v-else-if="schema[key].type === 'object' && key !== 'dsl_exit'"
                class="input w-full font-mono text-xs h-20 mt-1"
                :value="JSON.stringify(config[key], null, 2)"
                @input="(e) => { try { config[key] = JSON.parse((e.target as HTMLTextAreaElement).value); } catch {} }"
              ></textarea>

              <!-- 字符串 -->
              <input v-else class="input w-full mt-1" v-model="config[key]" />
            </div>
          </div>
        </div>

        <!-- DSL 退出策略 -->
        <div v-if="hasDslExit" class="card">
          <div class="flex items-center gap-2 mb-3 pb-2 border-b border-[var(--border)]">
            <span>◉</span>
            <span class="font-semibold">DSL 退出策略</span>
            <span class="ml-auto text-[10px] text-[var(--text-muted)]">8 项</span>
          </div>
          <div class="space-y-2.5">
            <div v-for="(dm, dk) in DSL_META" :key="dk">
              <label class="flex items-center gap-1.5 text-sm">
                <span>{{ dm.label }}</span>
                <span class="text-[10px] text-[var(--text-muted)] font-mono">{{ dk }}</span>
              </label>
              <input
                type="number"
                step="0.1"
                class="input w-full mt-1"
                :value="dslExit[dk]"
                @input="(e) => { if (!config.dsl_exit) config.dsl_exit = {}; config.dsl_exit[dk] = parseFloat((e.target as HTMLInputElement).value); }"
              />
            </div>
          </div>
        </div>
      </div>

      <!-- 备份与历史 -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <!-- 手动快照 -->
        <div class="card">
          <div class="flex items-center justify-between mb-3">
            <h3 class="font-semibold">📸 手动备份快照</h3>
            <span class="text-[10px] text-[var(--text-muted)]">保留最近 20 份</span>
          </div>
          <div v-if="snapshots.length === 0" class="text-sm text-[var(--text-muted)] py-4 text-center">
            暂无手动备份，点击右上角「手动备份」创建
          </div>
          <ul v-else class="space-y-2 text-sm">
            <li v-for="s in snapshots" :key="s.id" class="flex items-center justify-between border-b border-[var(--border)] pb-2">
              <div>
                <div class="font-mono text-xs">{{ s.id }}</div>
                <div class="text-xs text-[var(--text-muted)] mt-0.5">{{ formatTs(s.ts) }} · {{ s.reason || 'manual' }}</div>
              </div>
              <button class="btn text-xs" @click="rollback(s.id)">↩️ 恢复</button>
            </li>
          </ul>
          <div class="mt-3 pt-2 border-t border-[var(--border)]">
            <button class="btn text-xs w-full" @click="rollback()">↩️ 回滚到上次自动备份 (.bak)</button>
          </div>
        </div>

        <!-- 变更历史 -->
        <div class="card">
          <h3 class="font-semibold mb-3">📋 配置变更历史</h3>
          <div v-if="history.length === 0" class="text-sm text-[var(--text-muted)] py-4 text-center">暂无变更记录</div>
          <ul v-else class="space-y-2 text-sm max-h-72 overflow-y-auto">
            <li v-for="(h, i) in [...history].reverse()" :key="i" class="border-b border-[var(--border)] pb-2">
              <div class="flex items-center gap-2">
                <span class="text-[10px] px-1.5 py-0.5 rounded"
                  :class="h.event === 'config_snapshot' ? 'bg-blue-500/20 text-blue-300'
                    : h.event === 'config_rollback' ? 'bg-amber-500/20 text-amber-300'
                    : 'bg-emerald-500/20 text-emerald-300'">
                  {{ h.event === 'config_update' ? '变更' : h.event === 'config_rollback' ? '回滚' : '备份' }}
                </span>
                <span class="text-xs text-[var(--text-muted)]">{{ formatTs(h.ts) }}</span>
              </div>
              <div class="text-xs mt-1 text-[var(--text-2)]">{{ eventLabel(h) }}</div>
            </li>
          </ul>
        </div>
      </div>
    </template>
  </div>
</template>
