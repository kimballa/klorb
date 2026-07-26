// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import { formatSessionStats } from 'host/features/sessionControls';

describe('formatSessionStats', () => {
  it('extracts message/tool-call counts', () => {
    const data = formatSessionStats({
      user_messages: 3,
      response_messages: 3,
      thinking_messages: 1,
      tool_calls: 4,
      unknown_tool_calls: 0,
      malformed_tool_calls: 0,
    });

    expect(data.messageCounts).toEqual({
      'User messages': 3,
      'Response messages': 3,
      'Thinking messages': 1,
      'Total tool calls': 4,
      'Unknown tool calls': 0,
      'Malformed tool calls': 0,
    });
  });

  it('sorts the per-tool breakdown by name', () => {
    const data = formatSessionStats({
      tools: {
        Grep: { success_count: 2, failed_count: 0 },
        Bash: { success_count: 3, failed_count: 1 },
      },
    });

    expect(data.toolBreakdown).toEqual([
      { name: 'Bash', succeeded: 3, failed: 1 },
      { name: 'Grep', succeeded: 2, failed: 0 },
    ]);
  });

  it('omits the tool breakdown entirely when no tools ran', () => {
    const data = formatSessionStats({});
    expect(data.toolBreakdown).toEqual([]);
  });

  it('derives uncached/total/in+out tokens and the cache percentage', () => {
    const data = formatSessionStats({
      input_tokens: 1000,
      output_tokens: 200,
      cached_tokens: 400,
      total_cost: 0.015,
    });

    expect(data.tokenUsage).toEqual({
      'Input tokens': 1000,
      'Cached tokens': 400,
      'Uncached tokens': 600,
      'Output tokens': 200,
      'Total tokens': 1200,
      'In+out tokens': 800,
    });
    expect(data.cachePercent).toBe(40);
    expect(data.totalCost).toBe(0.015);
  });

  it('reports a 0% cache rate (not NaN) when input_tokens is 0', () => {
    const data = formatSessionStats({});
    expect(data.cachePercent).toBe(0);
  });

  it('floors uncached tokens at 0 if cached somehow exceeds input', () => {
    const data = formatSessionStats({ input_tokens: 100, cached_tokens: 150 });
    expect(data.tokenUsage['Uncached tokens']).toBe(0);
  });

  it('tolerates a missing/malformed result without throwing', () => {
    expect(() => formatSessionStats({})).not.toThrow();
  });
});
