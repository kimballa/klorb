// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import { formatTokenCount } from 'webview/formatTokenCount';

describe('formatTokenCount', () => {
  it('renders counts under 1000 as a literal integer', () => {
    expect(formatTokenCount(0)).toBe('0');
    expect(formatTokenCount(999)).toBe('999');
  });

  it('renders thousands at 2 significant figures with a k suffix', () => {
    expect(formatTokenCount(1400)).toBe('1.4k');
    expect(formatTokenCount(23000)).toBe('23k');
    expect(formatTokenCount(423000)).toBe('420k');
  });

  it('renders millions with an M suffix', () => {
    expect(formatTokenCount(1_400_000)).toBe('1.4M');
    expect(formatTokenCount(23_000_000)).toBe('23M');
  });

  it('rounds up into the next magnitude when 2-sig-fig rounding crosses 1000', () => {
    expect(formatTokenCount(999_600)).toBe('1M');
  });
});
