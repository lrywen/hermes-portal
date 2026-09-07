/**
 * Trader SSE 事件 → Alert 弹窗/语音桥接。
 *
 * trader 端 session_log 产生的事件（execute / ai_close / dsl_exit / research …）
 * 原本只进入 Channels 页面的事件列表，不会触发全局弹窗与提示音。
 * 这里按 notify_dispatch.py 一致的高信号分类，把它们映射成 alertStore.emit()
 * 可识别的 AlertEventPayload。是否真正弹窗/播音由 alertStore 根据用户订阅
 * 的 event_types 自行过滤，本模块只负责"翻译"。
 */
import { useAlertStore } from '@/stores/alerts';

type AlertLevel = 'info' | 'success' | 'warn' | 'danger';

interface MappedEvent {
  type: string;
  level: AlertLevel;
  title: string;
  content: string;
}

const ACTIONABLE_VERDICTS = new Set(['LONG', 'SHORT', 'CLOSE']);
const VERDICT_CN: Record<string, string> = { LONG: '做多', SHORT: '做空', CLOSE: '平仓' };
const SIDE_CN: Record<string, string> = { LONG: '做多', SHORT: '做空', BUY: '买入', SELL: '卖出' };
const DANGER_ERROR_SCOPES = new Set(['watchdog', 'dsl_monitor']);

function sideCN(side: unknown): string {
  if (typeof side !== 'string') return '—';
  return SIDE_CN[side.toUpperCase()] ?? side;
}

function fmtUSD(v: unknown): string {
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : '—';
}

function fmtPct(v: unknown, digits = 2): string {
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? `${n >= 0 ? '+' : ''}${n.toFixed(digits)}%` : '—';
}

/**
 * 把一条 trader 事件翻译成 Alert payload；返回 null 表示该事件类型不应弹窗
 * （scan / loop_heartbeat / ta_skip / near_miss 等高频噪音）。
 */
export function mapTraderEvent(data: any): MappedEvent | null {
  if (!data || typeof data !== 'object') return null;
  const event = String(data.event ?? '');
  const coin = data.coin ? String(data.coin) : '';

  switch (event) {
    case 'execute': {
      if (data.executed) {
        const parts = [
          `方向 ${sideCN(data.side)}`,
          `金额 ${fmtUSD(data.size_usd)}`,
          `入场价 ${data.entry_px ?? '—'}`,
        ];
        if (data.stop_px != null) parts.push(`止损 ${data.stop_px}`);
        if (data.tp_px != null) parts.push(`止盈 ${data.tp_px}`);
        if (data.regime) parts.push(`市场状态 ${data.regime}`);
        return {
          type: 'order_filled',
          level: 'success',
          title: `开仓成交 — ${coin || '未知币种'}`,
          content: parts.join('，'),
        };
      }
      const blocked = Array.isArray(data.blocked_by) ? data.blocked_by.join(', ') : (data.blocked_by || data.detail || '未知');
      return {
        type: 'risk_alert',
        level: 'warn',
        title: `开仓被风控拦截 — ${coin || '未知币种'}`,
        content: `方向 ${sideCN(data.side)}，原因 ${blocked}`,
      };
    }

    case 'ai_close': {
      const result = data.executed ? '已平仓' : '未成交/无需平仓';
      const reasoning = (data.reasoning || '').toString().trim();
      return {
        type: 'position_closed',
        level: 'info',
        title: `AI 决策平仓 — ${coin || '未知币种'}`,
        content: reasoning ? `${result}，理由：${reasoning.slice(0, 120)}` : result,
      };
    }

    case 'dsl_exit': {
      const realized = Number(data.realized_pnl_pct);
      const level: AlertLevel = Number.isFinite(realized) && realized < 0 ? 'danger' : 'warn';
      const parts = [
        `方向 ${sideCN(data.side)}`,
        `触发原因 ${data.reason || '—'}`,
        `仓位盈亏 ${fmtPct(data.leveraged_pct)}`,
        `已实现盈亏 ${fmtPct(data.realized_pnl_pct)}`,
      ];
      return {
        type: 'stop_loss_triggered',
        level,
        title: `止损/止盈平仓 — ${coin || '未知币种'}`,
        content: parts.join('，'),
      };
    }

    case 'hard_killswitch':
      return {
        type: 'circuit_breaker',
        level: 'danger',
        title: '硬日亏熔断触发 — 已全仓平仓',
        content: `当日盈亏 ${fmtUSD(data.daily_pnl)}，亏损上限 ${fmtUSD(data.limit)}，强平仓位 ${data.flattened ?? '—'}`,
      };

    case 'risk_gate_blind':
      // Audit 2026-09-07 (M3): 闸门读状态失败、fail-open 放行中（保护暂时失效）。
      // 复用 alerts 目录已有的 risk_alert code，免改 seed.py；danger 需手动关闭。
      // trader 端该事件 operator-only（不在 _PUBLIC_FEED_EVENTS），无权限用户收不到。
      return {
        type: 'risk_alert',
        level: 'danger',
        title: '风控闸门盲跑告警（fail-open 放行中）',
        content: `熔断门 ${data.gate ?? '—'}（币种 ${coin || '—'}）读状态失败，保护暂时失效：${data.error ?? '—'}`,
      };

    case 'place_order':
      return {
        type: 'order_filled',
        level: 'info',
        title: `手动下单 — ${coin || '未知币种'}`,
        content: `方向 ${sideCN(data.side)}，结果 ${data.ok ? '成功' : '失败'}`,
      };

    case 'close_position':
      return {
        type: 'position_closed',
        level: 'info',
        title: `手动平仓 — ${coin || '未知币种'}`,
        content: `结果 ${data.ok ? '成功' : '失败'}`,
      };

    case 'research': {
      const verdict = String(data.verdict || '').toUpperCase();
      if (!ACTIONABLE_VERDICTS.has(verdict)) return null;
      const reasoning = (data.reasoning || '').toString().trim();
      const parts = [
        `结论 ${VERDICT_CN[verdict] || verdict}`,
        `置信度 ${data.confidence != null ? `${Number(data.confidence).toFixed(2)}` : '—'}`,
      ];
      if (data.entry_px) parts.push(`建议入场 ${data.entry_px}`);
      if (data.stop_px) parts.push(`止损 ${data.stop_px}`);
      if (data.tp_px) parts.push(`止盈 ${data.tp_px}`);
      return {
        type: 'agent_signal',
        level: 'info',
        title: `AI 决策 — ${VERDICT_CN[verdict] || verdict} ${coin || '未知币种'}`,
        content: reasoning ? `${parts.join('，')}，理由：${reasoning.slice(0, 120)}` : parts.join('，'),
      };
    }

    case 'error': {
      const scope = data.scope || data.coin || 'loop';
      return {
        type: 'system_error',
        level: DANGER_ERROR_SCOPES.has(scope) ? 'danger' : 'warn',
        title: `系统错误 — ${scope}`,
        content: String(data.error || data.detail || '未知错误').slice(0, 200),
      };
    }

    case 'loop_start':
      return {
        type: 'mode_changed',
        level: 'info',
        title: 'Hermes 交易系统已启动',
        content: `模式 ${data.config?.mode || '—'}，扫描间隔 ${data.scan_interval ?? '—'}s`,
      };

    case 'loop_stop':
      return {
        type: 'mode_changed',
        level: 'warn',
        title: '交易循环已停止',
        content: data.reason ? `原因：${data.reason}` : '用户手动停止',
      };

    case 'ws_status': {
      // Phase 4 P0-3：行情馈送状态边沿事件（后端已做 30s 滞回去抖）。
      // 恢复(ok)不弹窗不播音——指示灯转绿即可；降级/中断才告警。
      const status = String(data.status ?? '').toLowerCase();
      const ageTxt = (v: unknown) =>
        typeof v === 'number' && Number.isFinite(v) ? `${v.toFixed(0)}s` : '无数据';
      if (status === 'down') {
        return {
          type: 'feed_status',
          level: 'danger',
          title: '行情馈送中断 — 已停止开新仓',
          content: `WS 与 REST 均无有效行情，系统 fail-closed 停止开新仓。WS 龄期 ${ageTxt(data.ws_age_s)}，REST 龄期 ${ageTxt(data.rest_age_s)}，请立即检查网络。`,
        };
      }
      if (status === 'degraded') {
        return {
          type: 'feed_status',
          level: 'warn',
          title: '行情馈送降级 — 已切换 REST 轮询',
          content: `WS allMids 中断，扫描降速并回退 REST 快照，平仓感知延迟增大。WS 龄期 ${ageTxt(data.ws_age_s)}，REST 龄期 ${ageTxt(data.rest_age_s)}。`,
        };
      }
      // ok / unknown：静默（指示灯由 sseFeed 订阅单独处理）
      return null;
    }

    // scan / ta_skip / near_miss / loop_heartbeat 等高频事件有意忽略
    default:
      return null;
  }
}

/**
 * 在 Channels.vue 的 SSE onmessage 中调用：若该 trader 事件被映射为 Alert，
 * 则派发到 alertStore，由其按用户订阅决定是否弹窗+播报。
 */
export function bridgeTraderEvent(data: any) {
  const mapped = mapTraderEvent(data);
  if (!mapped) {
    console.log('[TraderEventBridge]', '事件', data?.event, '无需弹窗（mapTraderEvent 返回 null）');
    return;
  }
  console.log('[TraderEventBridge]', '映射结果:', data?.event, '→', mapped.type, '/', mapped.level, mapped.title);
  try {
    const alerts = useAlertStore();
    alerts.emit({
      type: mapped.type,
      level: mapped.level,
      title: mapped.title,
      content: mapped.content,
      ts: data.ts || data.timestamp || Date.now(),
    });
  } catch (e) {
    // 桥接失败不应影响 SSE 主流程
    console.warn('[TraderEventBridge] emit 失败：', e);
  }
}
