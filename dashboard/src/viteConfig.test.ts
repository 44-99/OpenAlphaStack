import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

describe('dashboard production asset paths', () => {
  it('keeps Vite assets under the FastAPI /dashboard/assets mount', () => {
    const source = readFileSync(new URL('../vite.config.ts', import.meta.url), 'utf8');

    expect(source).toMatch(/base:\s*['"]\/dashboard\/['"]/);
    expect(source).not.toMatch(/base:\s*['"]\/dashboard\/assets\/['"]/);
  });
});
