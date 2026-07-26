// © Copyright 2026 Aaron Kimball
import type { SessionStatsMessage } from 'shared/webviewMessages';

/** `formatSessionStats()`'s return shape -- a `SessionStatsMessage` minus the message
 * envelope, since `showSessionStatsCommand` just spreads it into one when posting to the
 * webview (see `CommandsVsCode.postSessionStats`). */
export type SessionStatsData = Omit<SessionStatsMessage, 'type'>;

function num(value: unknown): number {
  return typeof value === 'number' ? value : 0;
}

/** Extracts a `_klorb/sessionStats` result (`Session.statistics.model_dump(mode="json")`
 * verbatim -- snake_case field names, see docs/specs/klorb-server.md) into the structured,
 * display-ready shape `SessionStatsCard` renders: `messageCounts`/`tokenUsage` are ordered
 * label -> value maps, `cachePercent`/`uncachedTokens`(folded into `tokenUsage`)/`inOutTokens`
 * (ditto) are derived the same way `klorb.session_statistics.SessionStatistics.
 * format_report()` derives them for the TUI's own rendering, and `toolBreakdown` is the
 * sorted per-tool success/failure list. This function does no formatting (no comma grouping,
 * no right-justification, no rules) -- that's `SessionStatsCard`'s job, once the data crosses
 * into the webview as a `SessionStatsMessage`. */
export function formatSessionStats(stats: Record<string, unknown>): SessionStatsData {
  const inputTokens = num(stats.input_tokens);
  const outputTokens = num(stats.output_tokens);
  const cachedTokens = num(stats.cached_tokens);
  const uncachedTokens = Math.max(inputTokens - cachedTokens, 0);
  const totalTokens = inputTokens + outputTokens;
  const inOutTokens = uncachedTokens + outputTokens;
  const cachePercent = inputTokens > 0 ? (100 * cachedTokens) / inputTokens : 0;

  const tools = stats.tools;
  const toolBreakdown =
    typeof tools === 'object' && tools !== null
      ? Object.keys(tools)
          .sort()
          .map((name) => {
            const entry = (tools as Record<string, unknown>)[name];
            const entryRecord =
              typeof entry === 'object' && entry !== null ? (entry as Record<string, unknown>) : {};
            return {
              name,
              succeeded: num(entryRecord.success_count),
              failed: num(entryRecord.failed_count),
            };
          })
      : [];

  return {
    messageCounts: {
      'User messages': num(stats.user_messages),
      'Response messages': num(stats.response_messages),
      'Thinking messages': num(stats.thinking_messages),
      'Total tool calls': num(stats.tool_calls),
      'Unknown tool calls': num(stats.unknown_tool_calls),
      'Malformed tool calls': num(stats.malformed_tool_calls),
    },
    toolBreakdown,
    tokenUsage: {
      'Input tokens': inputTokens,
      'Cached tokens': cachedTokens,
      'Uncached tokens': uncachedTokens,
      'Output tokens': outputTokens,
      'Total tokens': totalTokens,
      'In+out tokens': inOutTokens,
    },
    cachePercent,
    totalCost: num(stats.total_cost),
  };
}
