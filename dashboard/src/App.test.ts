import { describe, expect, it } from 'vitest';
import { chooseStockSearchResult, isOutsideRunControl } from './App';

describe('run control popover', () => {
  it('closes for outside pointer targets only', () => {
    const insideTarget = {};
    const outsideTarget = {};
    const control = {
      contains: (target: unknown) => target === insideTarget,
    };

    expect(isOutsideRunControl(outsideTarget as EventTarget, control)).toBe(true);
    expect(isOutsideRunControl(insideTarget as EventTarget, control)).toBe(false);
  });

  it('does not close when event target or control root is missing', () => {
    const control = { contains: () => false };

    expect(isOutsideRunControl(null, control)).toBe(false);
    expect(isOutsideRunControl({} as EventTarget, null)).toBe(false);
  });
});

describe('stock search selection', () => {
  const results = [
    { code: '000001', name: '平安银行', market: 'sz' as const },
    { code: '001359', name: '平安电工', market: 'sz' as const },
  ];

  it('prefers an exact stock code match', () => {
    expect(chooseStockSearchResult('001359', results)?.name).toBe('平安电工');
  });

  it('prefers an exact Chinese name and does not auto-select an ambiguous prefix', () => {
    expect(chooseStockSearchResult('平安银行', results)?.code).toBe('000001');
    expect(chooseStockSearchResult('平安', results)).toBeUndefined();
  });
});
