/**
 * ShadowBook 双账户组件测试
 *
 * 风险项 #1 的回归护栏：此前 maker UI 仅靠 vue-tsc 类型检查，无法保证真实
 * 渲染与切换行为。这里 mock 掉 http（BFF 代理）与 SSE，挂载组件后断言：
 *  - taker 默认视图渲染
 *  - maker 逆向选择/持仓/挂单区块根据后端字段渲染
 *  - 流水 taker/maker 切换会换数据源
 *  - maker 未启用（后端关闭）时不渲染 maker 区块
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';

// vi.mock 会被提升到文件顶部，故工厂内引用的可变状态用 vi.hoisted 提前建立；
// stub 组件直接在 mock 工厂内部创建（工厂里才能安全 import vue）。
const responsesHolder = vi.hoisted(() => ({ current: {} as Record<string, any> }));

// vue-echarts 在 happy-dom 下无需真实画布，工厂内创建占位组件。
vi.mock('vue-echarts', async () => {
  const { defineComponent, h } = await import('vue');
  return { default: defineComponent({ name: 'VChart', setup: () => () => h('div', 'chart') }) };
});

// http mock：每个端点返回预置响应，由 payload() 写入 holder。
vi.mock('@/shared/api/client', () => ({
  default: {
    get: vi.fn(async (url: string) => ({ data: responsesHolder.current[url] })),
    post: vi.fn(async () => ({ data: {} })),
  },
}));

// SSE store stub：onEvent 返回空退订，避免建立真实 EventSource。
vi.mock('@/stores/sseFeed', () => ({
  useSseFeedStore: () => ({ onEvent: () => () => undefined }),
}));

// toast store stub：避免触发 Pinia。
vi.mock('@/stores/toast', () => ({
  useToast: () => ({ ok: () => undefined, err: () => undefined }),
}));

// auth store stub：放开 shadow 只读与管理权限判断。
vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({
    hasPermission: (p: string) => p === 'shadow:read' || p === 'shadow:manage',
  }),
}));

import ShadowBook from './ShadowBook.vue';
import http from '@/shared/api/client';

const A = '/api/portal/trader/api/dashboard/shadow';

function payload(makerEnabled: boolean) {
  responsesHolder.current = {
    [`${A}/account`]: {
      enabled: true,
      maker_shadow_enabled: makerEnabled,
      starting_balance: 20,
      equity: 21,
      wallet_balance: 20,
      positions: [],
      maker_positions: makerEnabled
        ? [{ id: 'mp1', coin: 'BTC', side: 'long', size_usd: 100, entry_px: 100, unrealized_roe_pct: 1, unrealized_pnl_usd: 1 }]
        : [],
      maker_resting_orders: makerEnabled
        ? [{ id: 'ro1', coin: 'ETH', side: 'long', size_usd: 50, limit_px: 50, post_mid_px: 50.02, posted_at: Date.now() }]
        : [],
    },
    [`${A}/stats`]: {
      total_return_pct: 5,
      maker_shadow: makerEnabled
        ? {
            total_return_pct: 8,
            equity_usd: 21.6,
            adverse_selection: {
              fills: 3,
              cancels: 1,
              fill_rate_pct: 75,
              avg_maker_edge_bps: 4.2,
              avg_post_fill_drift_bps: 1.1,
              avg_resting_bars: 2,
            },
          }
        : null,
    },
    [`${A}/trades`]: {
      trades: [{ id: 't1', type: 'open', coin: 'BTC', side: 'long' }],
      maker_trades: makerEnabled
        ? [{ id: 'm1', type: 'open', coin: 'BTC', side: 'long', maker_edge_bps: 5, post_fill_mid_drift_bps: 1 }]
        : [],
    },
    [`${A}/equity-curve`]: {
      points: [{ ts: Date.now(), equity: 21 }],
      maker_points: makerEnabled ? [{ ts: Date.now(), equity: 21.6 }] : [],
    },
  };
}

async function mountBook() {
  const wrapper = mount(ShadowBook, { global: { stubs: { VChart: true } } });
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ShadowBook 双账户', () => {
  it('maker 启用时渲染逆向选择、maker 持仓与挂单区块', async () => {
    payload(true);
    const w = await mountBook();
    expect(w.text()).toContain('Maker 成交质量 / 逆向选择');
    expect(w.text()).toContain('Maker 当前持仓');
    expect(w.text()).toContain('Maker 挂单中');
    // 逆向选择聚合值来自 stats.maker_shadow.adverse_selection
    expect(w.text()).toContain('75');
    expect(w.text()).toContain('4.2');
  });

  it('流水切换到 maker 时使用 maker_trades 数据', async () => {
    payload(true);
    const w = await mountBook();
    // 默认 taker
    expect((w.vm as any).fillAccount).toBe('taker');
    const makerBtn = w.findAll('button').find((b) => b.text() === 'Maker');
    expect(makerBtn).toBeTruthy();
    await makerBtn!.trigger('click');
    await flushPromises();
    expect((w.vm as any).fillAccount).toBe('maker');
    // maker 开仓行在“原因/时长”列展示 edge/drift
    expect(w.text()).toContain('edge');
  });

  it('maker 关闭时不渲染 maker 专属区块', async () => {
    payload(false);
    const w = await mountBook();
    expect(w.text()).not.toContain('Maker 成交质量 / 逆向选择');
    expect(w.text()).not.toContain('Maker 挂单中');
  });

  it('挂载时拉取四个 shadow 端点', async () => {
    payload(true);
    await mountBook();
    const urls = (http.get as any).mock.calls.map((c: any[]) => c[0]);
    for (const tail of ['/account', '/stats', '/trades', '/equity-curve']) {
      expect(urls).toContain(`${A}${tail}`);
    }
  });
});
