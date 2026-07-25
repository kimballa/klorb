// © Copyright 2026 Aaron Kimball
import type { DiffHunk } from 'shared/webviewMessages';

/** One row `ToolCallChip`'s diff view maps to a rendered line: a `'context'`/`'add'`/`'del'`
 * row from a hunk's own lines, or a `'hunk-separator'` row (no text/line numbers) inserted
 * between two hunks so a multi-hunk diff reads as visually distinct blocks, matching a
 * standard unified diff. */
export interface DiffRow {
  kind: 'context' | 'add' | 'del' | 'hunk-separator';
  oldLineno: number | null;
  newLineno: number | null;
  text: string;
}

/**
 * Flattens `hunks` into the row models `ToolCallChip` renders as a colored, gutter-numbered
 * diff view -- a hunk's own `lines` verbatim, with a `'hunk-separator'` row inserted between
 * consecutive hunks (never before the first or after the last).
 */
export function renderDiffLines(hunks: readonly DiffHunk[]): DiffRow[] {
  const rows: DiffRow[] = [];
  hunks.forEach((hunk, index) => {
    if (index > 0) {
      rows.push({ kind: 'hunk-separator', oldLineno: null, newLineno: null, text: '' });
    }
    for (const line of hunk.lines) {
      rows.push({
        kind: line.kind,
        oldLineno: line.oldLineno,
        newLineno: line.newLineno,
        text: line.text,
      });
    }
  });
  return rows;
}
