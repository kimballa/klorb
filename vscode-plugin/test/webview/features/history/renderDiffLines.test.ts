// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import { renderDiffLines } from 'webview/features/history';

describe('renderDiffLines', () => {
  it('flattens a single hunk into context/add/del rows with gutter numbers', () => {
    // eslint-disable-next-line testing-library/render-result-naming-convention -- pure function, not @testing-library/react's render()
    const rows = renderDiffLines([
      {
        lines: [
          { kind: 'context', oldLineno: 1, newLineno: 1, text: 'unchanged' },
          { kind: 'del', oldLineno: 2, newLineno: null, text: 'removed' },
          { kind: 'add', oldLineno: null, newLineno: 2, text: 'added' },
        ],
      },
    ]);
    expect(rows).toEqual([
      { kind: 'context', oldLineno: 1, newLineno: 1, text: 'unchanged' },
      { kind: 'del', oldLineno: 2, newLineno: null, text: 'removed' },
      { kind: 'add', oldLineno: null, newLineno: 2, text: 'added' },
    ]);
  });

  it('inserts a hunk-separator row between (but not around) multiple hunks', () => {
    // eslint-disable-next-line testing-library/render-result-naming-convention -- pure function, not @testing-library/react's render()
    const rows = renderDiffLines([
      { lines: [{ kind: 'add', oldLineno: null, newLineno: 1, text: 'first hunk' }] },
      { lines: [{ kind: 'add', oldLineno: null, newLineno: 40, text: 'second hunk' }] },
      { lines: [{ kind: 'add', oldLineno: null, newLineno: 80, text: 'third hunk' }] },
    ]);
    expect(rows.map((row) => row.kind)).toEqual([
      'add',
      'hunk-separator',
      'add',
      'hunk-separator',
      'add',
    ]);
    expect(rows[0]?.text).toBe('first hunk');
    expect(rows[2]?.text).toBe('second hunk');
    expect(rows[4]?.text).toBe('third hunk');
  });

  it('returns no rows for no hunks', () => {
    expect(renderDiffLines([])).toEqual([]);
  });
});
