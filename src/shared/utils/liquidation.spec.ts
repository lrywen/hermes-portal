/**
 * 强平价距离计算单元测试（O-4 回移植自 hermes-web）。
 * 纯函数，node 环境即可，无需 DOM。
 */
import { describe, expect, it } from 'vitest';
import { liqDistPct, liqLevel } from './liquidation';

describe('liqDistPct', () => {
  it('liq_px 为 null（完全保证金）时返回 null', () => {
    expect(liqDistPct({ side: 'long', mark_px: 3000, liq_px: null })).toBeNull();
    expect(liqDistPct({ side: 'long', mark_px: 3000, liq_px: undefined })).toBeNull();
  });

  it('mark_px 为 0/null/undefined 时返回 null（防除零）', () => {
    expect(liqDistPct({ side: 'long', mark_px: 0, liq_px: 2000 })).toBeNull();
    expect(liqDistPct({ side: 'short', mark_px: null, liq_px: 4000 })).toBeNull();
    expect(liqDistPct({ side: 'long', mark_px: undefined, liq_px: 2000 })).toBeNull();
  });

  it('多仓：强平价在下方，距离 = (mark - liq) / mark', () => {
    // (3000 - 2700) / 3000 = 10%
    expect(liqDistPct({ side: 'long', mark_px: 3000, liq_px: 2700 })).toBeCloseTo(10, 6);
    // (3000 - 2400) / 3000 = 20%
    expect(liqDistPct({ side: 'long', mark_px: 3000, liq_px: 2400 })).toBeCloseTo(20, 6);
  });

  it('空仓：强平价在上方，距离 = (liq - mark) / mark', () => {
    // (3300 - 3000) / 3000 = 10%
    expect(liqDistPct({ side: 'short', mark_px: 3000, liq_px: 3300 })).toBeCloseTo(10, 6);
    // (3600 - 3000) / 3000 = 20%
    expect(liqDistPct({ side: 'short', mark_px: 3000, liq_px: 3600 })).toBeCloseTo(20, 6);
  });

  it('结果始终为正百分比', () => {
    expect(liqDistPct({ side: 'long', mark_px: 100, liq_px: 90 })).toBeGreaterThan(0);
    expect(liqDistPct({ side: 'short', mark_px: 100, liq_px: 110 })).toBeGreaterThan(0);
  });
});

describe('liqLevel', () => {
  it('null 输入返回 null', () => {
    expect(liqLevel(null)).toBeNull();
    expect(liqLevel(undefined as unknown as null)).toBeNull();
  });

  it('<10% 为 danger（红色高危）', () => {
    expect(liqLevel(0)).toBe('danger');
    expect(liqLevel(9.99)).toBe('danger');
  });

  it('10% 边界为 warn（<10 才 danger）', () => {
    expect(liqLevel(10)).toBe('warn');
    expect(liqLevel(15)).toBe('warn');
    expect(liqLevel(19.99)).toBe('warn');
  });

  it('20% 边界为 safe（<20 才 warn）', () => {
    expect(liqLevel(20)).toBe('safe');
    expect(liqLevel(50)).toBe('safe');
  });
});
