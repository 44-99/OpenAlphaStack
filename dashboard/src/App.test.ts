import { describe, expect, it } from 'vitest';
import { isOutsideRunControl } from './App';

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
