/**
 * 强平价距离计算（O-4 回移植自 hermes-web）。
 *
 * liq_px 来自 HL 的 liquidationPx，完全保证金/极小仓位时为 null → 不参与计算。
 * 距强平距离按方向计算（结果始终为正的百分比）：
 *   多仓：强平价在下方，距离 = (mark - liq) / mark；
 *   空仓：强平价在上方，距离 = (liq - mark) / mark。
 *
 * 阈值（与预警卡片、持仓表共用）：<10% 红色高危，<20% 琥珀注意，其余安全。
 */
export type LiqLevel = 'danger' | 'warn' | 'safe';

export function liqDistPct(p: {
  side: string;
  mark_px: number | null | undefined;
  liq_px: number | null | undefined;
}): number | null {
  if (p.liq_px == null || !p.mark_px) return null;
  const dist =
    p.side === 'long'
      ? (p.mark_px - p.liq_px) / p.mark_px
      : (p.liq_px - p.mark_px) / p.mark_px;
  return dist * 100;
}

export function liqLevel(distPct: number | null): LiqLevel | null {
  if (distPct == null) return null;
  if (distPct < 10) return 'danger';
  if (distPct < 20) return 'warn';
  return 'safe';
}
