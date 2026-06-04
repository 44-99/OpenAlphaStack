import { describe, expect, it } from 'vitest';
import { shouldApplyTerminalInjection } from './AgentPanel';

describe('AgentPanel helpers', () => {
  it('injects context only into active terminals with a fresh id', () => {
    expect(shouldApplyTerminalInjection(true, 2, 1)).toBe(true);
    expect(shouldApplyTerminalInjection(false, 2, 1)).toBe(false);
    expect(shouldApplyTerminalInjection(true, 2, 2)).toBe(false);
    expect(shouldApplyTerminalInjection(true, undefined, 1)).toBe(false);
  });
});
