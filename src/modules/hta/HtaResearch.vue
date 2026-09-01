<template>
  <!--
    原生多视角研判面板（hermes-trader 进程内辩论提供）。
    - 输入币种 → POST /api/portal/trader/api/agent/research/{coin}
    - 渲染多空辩论观点、综合裁决、置信度、关键价位与风险
    - 加载骨架屏、异常重试、历史记录
  -->
  <div class="hta-page">
    <div class="page-head">
      <div>
        <h2>原生多视角研判</h2>
        <p class="sub">进程内多空辩论 + 结构化裁决</p>
      </div>
      <div class="head-actions">
        <span v-if="result" class="src-badges">
          <span class="src-badge" :class="{ on: result.debate_used }">辩论</span>
          <span class="src-badge" :class="{ on: result.structured }">结构化</span>
        </span>
      </div>
    </div>

    <div class="card control-card">
      <div class="control-row">
        <div class="coin-input-wrap">
          <input
            v-model="coinInput"
            class="input coin-input"
            placeholder="输入币种，如 BTC / ETH / SOL"
            @keyup.enter="runResearch"
            :disabled="loading"
          />
          <div v-if="suggestions.length" class="suggestions">
            <button
              v-for="s in suggestions"
              :key="s"
              class="sug-item"
              @click="coinInput = s; runResearch()"
            >
              {{ s }}
            </button>
          </div>
        </div>
        <button class="btn btn-primary" :disabled="loading || !coinInput.trim()" @click="runResearch">
          {{ loading ? '研判中…' : '开始研判' }}
        </button>
        <button class="btn" :disabled="loading" @click="reset">清空</button>
      </div>
      <div v-if="lastRunAt" class="last-run">
        上次研判：{{ coinInput.toUpperCase() }} · {{ formatTime(lastRunAt) }} · 耗时 {{ (elapsedMs / 1000).toFixed(1) }}s
      </div>
    </div>

    <!-- 降级/不可达提示 -->
    <div v-if="degradedNote" class="warn-banner">
      <span>⚠ {{ degradedNote }}</span>
    </div>

    <!-- 错误提示 -->
    <div v-if="error" class="error-banner">
      <span>⚠ {{ error }}</span>
      <button class="btn btn-ghost" @click="runResearch">重试</button>
    </div>

    <!-- 加载骨架 -->
    <div v-if="loading" class="card skeleton-card">
      <div class="skeleton skeleton-title" />
      <div class="skeleton skeleton-line w80" />
      <div class="skeleton skeleton-line w60" />
      <div class="skeleton skeleton-block" />
      <div class="skeleton skeleton-line w90" />
      <div class="skeleton skeleton-line w70" />
    </div>

    <!-- 研判结果 -->
    <div v-if="result && !loading" class="result-grid">
      <!-- 综合裁决 -->
      <div class="card verdict-card" :class="verdictClass">
        <div class="verdict-label">综合裁决</div>
        <div class="verdict-direction">
          <span class="verdict-badge" :class="verdictClass">{{ verdictLabel }}</span>
          <span class="verdict-ticker">{{ (result.coin || coinInput).toUpperCase() }}</span>
          <span v-if="result.as_of_date" class="verdict-date">{{ result.as_of_date }}</span>
        </div>
        <div v-if="decisionText" class="decision-text">{{ decisionText }}</div>
        <div v-if="result" class="verdict-meta">
          <span>置信度</span>
          <template v-if="result.confidence != null">
            <div class="conf-bar">
              <div class="conf-fill" :style="{ width: result.confidence + '%' }" />
            </div>
            <span>{{ result.confidence }}%</span>
          </template>
          <span v-else class="text-[var(--text-muted)]">—（AI 未给出置信度）</span>
        </div>
      </div>

      <!-- 核心指标 -->
      <div v-if="hasMetrics" class="card metrics-card">
        <h3>关键指标</h3>
        <div class="metric-grid">
          <div v-if="result.current_price != null && result.current_price > 0" class="metric">
            <div class="m-label">当前价格</div>
            <div class="m-value">{{ formatPrice(result.current_price) }}</div>
          </div>
          <div v-if="hasDirection" class="metric">
            <div class="m-label">建议方向</div>
            <div class="m-value" :class="directionClass">{{ directionLabel }}</div>
          </div>
          <div v-if="result.stop_loss != null && result.stop_loss > 0" class="metric">
            <div class="m-label">止损价</div>
            <div class="m-value danger">{{ formatPrice(result.stop_loss) }}</div>
          </div>
          <div v-if="result.take_profit != null && result.take_profit > 0" class="metric">
            <div class="m-label">止盈价</div>
            <div class="m-value success">{{ formatPrice(result.take_profit) }}</div>
          </div>
          <div v-if="result.suggested_stop_pct != null" class="metric">
            <div class="m-label">建议止损</div>
            <div class="m-value">{{ (result.suggested_stop_pct * 100).toFixed(2) }}%</div>
          </div>
          <div v-if="result.risk_level" class="metric">
            <div class="m-label">信念等级</div>
            <div class="m-value" :class="riskClass">{{ riskLabel }}</div>
          </div>
        </div>
      </div>

      <!-- 多空辩论观点 -->
      <div v-if="hasDebate" class="card analysts-card">
        <h3>多空辩论</h3>
        <div class="analyst-list debate-list">
          <div v-if="result.bull_case" class="analyst debate-bull">
            <div class="analyst-head">
              <span class="analyst-name">▲ 多头观点</span>
            </div>
            <p class="analyst-view">{{ result.bull_case }}</p>
          </div>
          <div v-if="result.bear_case" class="analyst debate-bear">
            <div class="analyst-head">
              <span class="analyst-name">▼ 空头观点</span>
            </div>
            <p class="analyst-view">{{ result.bear_case }}</p>
          </div>
        </div>
      </div>

      <!-- 风险提示 -->
      <div v-if="result.risks?.length" class="card risks-card">
        <h3>风险提示</h3>
        <ul>
          <li v-for="(r, i) in result.risks" :key="i">{{ r }}</li>
        </ul>
      </div>

      <!-- 原始 JSON -->
      <details class="card raw-card">
        <summary>查看原始响应</summary>
        <pre>{{ JSON.stringify(result, null, 2) }}</pre>
      </details>
    </div>

    <!-- 历史记录 -->
    <div v-if="history.length" class="card history-card">
      <div class="history-head">
        <h3>研判历史（{{ history.length }}）</h3>
        <button class="btn btn-ghost btn-sm" @click="clearHistory">清空历史</button>
      </div>
      <div class="history-list">
        <div
          v-for="h in history"
          :key="h.ts"
          class="history-item"
          @click="loadHistoryItem(h)"
        >
          <span class="h-coin">{{ h.coin }}</span>
          <span class="badge" :class="signalClass(h.decision)">{{ signalLabel(h.decision) }}</span>
          <span class="h-time">{{ formatTime(h.ts) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import http from '@/shared/api/client';

// 原生端点 POST /api/agent/research/{coin} 返回的 analysis 字典（仅声明前端用到的字段）
interface NativeAnalysis {
  coin?: string;
  verdict?: string;
  confidence?: number;       // 0-1
  side?: string | null;      // long / short / null
  entry_px?: number;
  stop_px?: number;
  tp_px?: number;
  reasoning?: string;
  conviction?: string | null;
  bull_case?: string;
  bear_case?: string;
  suggested_stop_pct?: number | null;
  key_risks?: string[];
  debate_used?: boolean;
  structured?: boolean;
  ai_down?: boolean;
  nlp_parsed?: boolean;
  degraded?: boolean;
  as_of_date?: string;
  [k: string]: any;
}

// 展示层形状：置信度折算为 0-100，价位字段对齐旧 UI
interface ResearchResult {
  coin?: string;
  verdict?: string;
  confidence?: number;       // 0-100
  reasoning?: string;
  current_price?: number;
  direction?: string | null;
  stop_loss?: number;
  take_profit?: number;
  risk_level?: string | null;
  risks?: string[];
  bull_case?: string;
  bear_case?: string;
  suggested_stop_pct?: number | null;
  debate_used?: boolean;
  structured?: boolean;
  ai_down?: boolean;
  degraded?: boolean;
  as_of_date?: string;
  [k: string]: any;
}
interface HistoryItem {
  ts: number;
  coin: string;
  result: ResearchResult;
  decision: string;
}

const HISTORY_KEY = 'native_research_history';
const HISTORY_MAX = 50;

const coinInput = ref('BTC');
const loading = ref(false);
const error = ref('');
const result = ref<ResearchResult | null>(null);
const lastRunAt = ref<number | null>(null);
const elapsedMs = ref(0);
const history = ref<HistoryItem[]>([]);

const suggestions = ['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE'];

// 将原生 analysis 映射为展示层 ResearchResult
function mapAnalysis(a: NativeAnalysis): ResearchResult {
  // 置信度三态 (supplemental audit 2026-08-31 F6)：上游真实给出数值（含合法
  // 的 0 置信度）才折算为 0-100；缺失（null/undefined/非数字）保留 undefined，
  // 由模板显示 '—'，不再把"无数据/AI 未给置信度"固化成 0。
  const conf = typeof a.confidence === 'number' && !Number.isNaN(a.confidence)
    ? Math.round(a.confidence * 100)
    : undefined;
  return {
    ...a,
    confidence: conf,
    current_price: a.entry_px ?? undefined,
    direction: a.side ?? null,
    stop_loss: a.stop_px ?? undefined,
    take_profit: a.tp_px ?? undefined,
    risk_level: a.conviction ?? null,
    risks: Array.isArray(a.key_risks) ? a.key_risks : [],
    bull_case: a.bull_case || '',
    bear_case: a.bear_case || '',
  };
}

const verdict = computed(() => {
  const r = result.value;
  if (!r) return '';
  const v = String(r.verdict || '').toLowerCase();
  if (v === 'long' || v === 'buy') return 'bullish';
  if (v === 'short' || v === 'sell') return 'bearish';
  if (v === 'pass' || v === 'hold' || v === 'neutral') return 'neutral';
  if (v === 'close') return 'close';
  // 兜底用 side
  const s = String(r.direction || '').toLowerCase();
  if (s === 'long') return 'bullish';
  if (s === 'short') return 'bearish';
  return v || 'neutral';
});
const verdictLabel = computed(() => signalLabel(verdict.value));
const verdictClass = computed(() => signalClass(verdict.value));
const decisionText = computed(() => result.value?.reasoning || '');
const hasDirection = computed(() => {
  const d = String(result.value?.direction || '').toLowerCase();
  return d === 'long' || d === 'short';
});
const hasMetrics = computed(() => {
  const r = result.value;
  if (!r) return false;
  return (r.current_price != null && r.current_price > 0)
    || hasDirection.value
    || (r.stop_loss != null && r.stop_loss > 0)
    || (r.take_profit != null && r.take_profit > 0)
    || r.suggested_stop_pct != null
    || !!r.risk_level;
});
const directionLabel = computed(() => {
  const d = String(result.value?.direction || '').toLowerCase();
  if (d === 'long') return '做多';
  if (d === 'short') return '做空';
  return '—';
});
const directionClass = computed(() => signalClass(directionLabel.value));
const riskLabel = computed(() => {
  const r = String(result.value?.risk_level || '').toLowerCase();
  if (r.includes('high') || r.includes('strong')) return '高';
  if (r.includes('med') || r.includes('mod')) return '中';
  if (r.includes('low')) return '低';
  return result.value?.risk_level || '—';
});
const riskClass = computed(() => {
  const r = String(result.value?.risk_level || '').toLowerCase();
  if (r.includes('high') || r.includes('strong')) return 'badge-danger';
  if (r.includes('med') || r.includes('mod')) return 'badge-warn';
  return 'badge-ok';
});
const hasDebate = computed(() => !!(result.value?.bull_case || result.value?.bear_case));
const degradedNote = computed(() => {
  const r = result.value;
  if (!r) return '';
  if (r.ai_down) return 'AI 服务不可达，返回保守观望裁决（PASS）。';
  if (r.degraded) return '研判处于降级模式，结论可靠性降低，请谨慎参考。';
  return '';
});

async function runResearch() {
  const coin = coinInput.value.trim().toUpperCase();
  if (!coin || loading.value) return;
  loading.value = true;
  error.value = '';
  result.value = null;
  const t0 = performance.now();
  const url = `/api/portal/trader/api/agent/research/${encodeURIComponent(coin)}`;
  // eslint-disable-next-line no-console
  console.info('[research] >> request', { method: 'POST', url, coin, timeoutMs: 90000 });
  try {
    // 原生端点为进程内同步辩论，LLM 调用可能耗时较久，放宽到 90s
    const resp = await http.post<NativeAnalysis>(url, {}, { timeout: 90000 });
    const raw: NativeAnalysis = resp.data ?? resp;
    // eslint-disable-next-line no-console
    console.info('[research] << response', {
      coin,
      status: (resp as any)?.status ?? 200,
      elapsedMs: Math.round(performance.now() - t0),
      verdict: raw.verdict,
      confidence: raw.confidence,
      debate_used: raw.debate_used,
      structured: raw.structured,
      degraded: raw.degraded,
      ai_down: raw.ai_down,
      keys: Object.keys(raw ?? {}),
    });
    result.value = mapAnalysis(raw);
    lastRunAt.value = Date.now();
    elapsedMs.value = performance.now() - t0;
    pushHistory(coin, result.value);
  } catch (e: any) {
    elapsedMs.value = performance.now() - t0;
    const status = e?.response?.status;
    const detail = e?.response?.data?.detail || e?.message || String(e);
    // eslint-disable-next-line no-console
    console.error('[research] !! error', {
      coin, status, detail, elapsedMs: Math.round(elapsedMs.value),
      responseData: e?.response?.data,
    });
    error.value = status ? `研判失败 (${status})：${detail}` : `研判失败：${detail}`;
  } finally {
    loading.value = false;
    // eslint-disable-next-line no-console
    console.info('[research] == done', { coin, elapsedMs: Math.round(performance.now() - t0) });
  }
}

function reset() {
  result.value = null;
  error.value = '';
  lastRunAt.value = null;
  elapsedMs.value = 0;
}

// ---- 历史记录 ----
function pushHistory(coin: string, r: ResearchResult) {
  const item: HistoryItem = {
    ts: Date.now(),
    coin,
    result: { ...r },
    decision: verdict.value,
  };
  history.value = [item, ...history.value.filter((h) => h.coin !== coin)].slice(0, HISTORY_MAX);
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history.value));
  } catch { /* quota / private mode */ }
}
function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (raw) history.value = JSON.parse(raw);
  } catch { /* ignore */ }
}
function loadHistoryItem(h: HistoryItem) {
  result.value = h.result;
  coinInput.value = h.coin;
  lastRunAt.value = h.ts;
}
function clearHistory() {
  history.value = [];
  try { localStorage.removeItem(HISTORY_KEY); } catch { /* ignore */ }
}

// ---- 展示辅助 ----
function signalLabel(s: string): string {
  switch (s) {
    case 'bullish': return '看多';
    case 'bearish': return '看空';
    case 'neutral': return '观望';
    case 'close': return '平仓';
    case '做多': return '做多';
    case '做空': return '做空';
    default: return s || '—';
  }
}
function signalClass(s: string): string {
  if (s === 'bullish' || s === '做多') return 'badge-ok';
  if (s === 'bearish' || s === '做空') return 'badge-danger';
  if (s === 'close') return 'badge-warn';
  return 'badge-neutral';
}
function formatPrice(p: number | null | undefined): string {
  if (p == null || isNaN(p as number)) return '—';
  const v = Number(p);
  if (v >= 1000) return v.toLocaleString('en-US', { maximumFractionDigits: 2 });
  if (v >= 1) return v.toFixed(2);
  return v.toPrecision(4);
}
function formatTime(ts: number | null): string {
  if (!ts) return '';
  const d = new Date(ts);
  return d.toLocaleString('zh-CN', { hour12: false });
}

onMounted(loadHistory);
</script>

<style scoped>
.hta-page { display: flex; flex-direction: column; gap: 16px; }
.page-head { display: flex; justify-content: space-between; align-items: flex-start; }
.page-head h2 { margin: 0; font-size: 20px; }
.sub { margin: 4px 0 0; color: var(--muted); font-size: 13px; }
.head-actions { display: flex; align-items: center; gap: 8px; }
.src-badges { display: flex; gap: 6px; }
.src-badge {
  font-size: 11px; padding: 2px 8px; border-radius: 10px;
  background: var(--surface-2); color: var(--muted); border: 1px solid transparent;
}
.src-badge.on { color: var(--accent); border-color: var(--accent); background: rgba(56, 189, 248, 0.1); }

.card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 18px;
}
.control-card { padding: 16px 18px; }
.control-row { display: flex; gap: 10px; align-items: center; }
.coin-input-wrap { position: relative; flex: 1; max-width: 420px; }
.coin-input { width: 100%; }
.input {
  background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
  padding: 9px 12px; color: var(--text); font-size: 14px; outline: none;
}
.input:focus { border-color: var(--accent); }
.suggestions {
  position: absolute; top: 100%; left: 0; right: 0; margin-top: 4px;
  display: flex; flex-wrap: wrap; gap: 6px; z-index: 5;
}
.sug-item {
  background: var(--surface-2); border: 1px solid var(--border); border-radius: 6px;
  padding: 3px 10px; font-size: 12px; cursor: pointer; color: var(--text);
}
.sug-item:hover { border-color: var(--accent); }
.btn {
  background: var(--surface-2); border: 1px solid var(--border); border-radius: 8px;
  padding: 9px 16px; color: var(--text); font-size: 14px; cursor: pointer;
}
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn-ghost { background: transparent; }
.last-run { margin-top: 10px; font-size: 12px; color: var(--muted); }

.error-banner, .warn-banner {
  display: flex; justify-content: space-between; align-items: center;
  padding: 10px 14px; border-radius: 8px; font-size: 13px;
}
.error-banner { background: rgba(239, 68, 68, 0.1); color: var(--danger); border: 1px solid rgba(239, 68, 68, 0.3); }
.warn-banner { background: rgba(245, 158, 11, 0.1); color: var(--warn); border: 1px solid rgba(245, 158, 11, 0.3); }

.skeleton-card { display: flex; flex-direction: column; gap: 10px; }
.skeleton { background: linear-gradient(90deg, var(--surface-2) 25%, var(--bg) 50%, var(--surface-2) 75%); background-size: 200% 100%; animation: shimmer 1.4s infinite; border-radius: 6px; }
@keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
.skeleton-title { height: 22px; width: 180px; }
.skeleton-line { height: 14px; }
.skeleton-block { height: 80px; }
.w80 { width: 80%; } .w60 { width: 60%; } .w90 { width: 90%; } .w70 { width: 70%; }

.result-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.verdict-card { grid-column: 1 / -1; }
.verdict-label { font-size: 12px; color: var(--muted); margin-bottom: 8px; }
.verdict-direction { display: flex; align-items: center; gap: 12px; }
.verdict-badge { font-size: 24px; font-weight: 800; padding: 4px 14px; border-radius: 8px; }
.verdict-badge.badge-ok { color: var(--success); background: rgba(34, 197, 94, 0.12); }
.verdict-badge.badge-danger { color: var(--danger); background: rgba(239, 68, 68, 0.12); }
.verdict-badge.badge-warn { color: var(--warn); background: rgba(245, 158, 11, 0.12); }
.verdict-badge.badge-neutral { color: var(--muted); background: var(--surface-2); }
.verdict-ticker { font-size: 16px; font-weight: 600; font-family: var(--mono); }
.verdict-date { font-size: 12px; color: var(--muted); }
.decision-text {
  margin: 12px 0 0; padding: 12px 16px; background: var(--bg);
  border-radius: 8px; font-size: 14px; line-height: 1.7; color: var(--text);
  white-space: pre-wrap; word-break: break-word;
}
.verdict-meta { display: flex; align-items: center; gap: 12px; font-size: 13px; color: var(--muted); margin-top: 12px; }
.conf-bar { width: 200px; height: 6px; background: var(--surface-2); border-radius: 3px; overflow: hidden; }
.conf-fill { height: 100%; background: var(--accent); transition: width 0.5s; }

.metrics-card, .analysts-card, .risks-card, .history-card, .raw-card { padding: 18px; }
.metrics-card h3, .analysts-card h3, .risks-card h3, .history-card h3 { margin: 0 0 14px; font-size: 15px; }
.history-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
.btn-sm { padding: 3px 10px; font-size: 12px; }
.metric-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.metric { background: var(--bg); padding: 12px; border-radius: 8px; }
.m-label { font-size: 11px; color: var(--muted); margin-bottom: 4px; }
.m-value { font-size: 18px; font-weight: 700; font-family: var(--mono); }
.m-value.success { color: var(--success); }
.m-value.danger { color: var(--danger); }
.badge { font-size: 12px; padding: 2px 8px; border-radius: 6px; font-weight: 600; }
.badge-ok { color: var(--success); background: rgba(34, 197, 94, 0.12); }
.badge-danger { color: var(--danger); background: rgba(239, 68, 68, 0.12); }
.badge-warn { color: var(--warn); background: rgba(245, 158, 11, 0.12); }
.badge-neutral { color: var(--muted); background: var(--surface-2); }

.analyst-list { display: flex; flex-direction: column; gap: 12px; }
.debate-list { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.analyst { background: var(--bg); padding: 14px; border-radius: 8px; border-left: 3px solid var(--border); }
.debate-bull { border-left-color: var(--success); }
.debate-bear { border-left-color: var(--danger); }
.analyst-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.analyst-name { font-weight: 600; font-size: 13px; }
.analyst-view { margin: 0; font-size: 13px; line-height: 1.7; color: var(--text); white-space: pre-wrap; word-break: break-word; }

.risks-card ul { margin: 0; padding-left: 20px; line-height: 1.8; font-size: 13px; color: var(--warn); }
.raw-card { grid-column: 1 / -1; }
.raw-card summary { cursor: pointer; font-size: 13px; color: var(--muted); }
.raw-card pre {
  margin-top: 12px; background: var(--bg); padding: 14px; border-radius: 8px;
  overflow-x: auto; font-size: 12px; font-family: var(--mono);
}
.history-list { display: flex; flex-direction: column; gap: 6px; }
.history-item {
  display: flex; align-items: center; gap: 12px; padding: 8px 12px;
  background: var(--bg); border-radius: 6px; cursor: pointer; font-size: 13px;
}
.history-item:hover { background: var(--surface-2); }
.h-coin { font-weight: 600; min-width: 60px; }
.h-time { margin-left: auto; color: var(--muted); font-size: 12px; }

@media (max-width: 900px) {
  .result-grid { grid-template-columns: 1fr; }
  .metric-grid { grid-template-columns: repeat(2, 1fr); }
  .debate-list { grid-template-columns: 1fr; }
}
</style>
