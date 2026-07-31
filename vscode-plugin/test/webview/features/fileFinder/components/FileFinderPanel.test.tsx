/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { FileFinderPanel } from 'webview/features/fileFinder';

beforeAll(() => {
  // jsdom doesn't implement scrollIntoView, which the panel calls to keep the active row
  // visible (see test/webview/App.test.tsx's own identical stub).
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

afterEach(cleanup);

const PATHS = ['README.md', 'src/webview/App.tsx', 'some/deeply/nested/path/to/file.txt'];

describe('FileFinderPanel', () => {
  it('renders a row per path, splitting nested paths into dir/file parts', () => {
    render(
      <FileFinderPanel
        paths={PATHS}
        activeIndex={0}
        onHover={() => undefined}
        onSelect={() => undefined}
      />
    );

    expect(screen.getByText('README.md')).toBeTruthy();
    expect(screen.getByText('/App.tsx')).toBeTruthy();
    expect(screen.getByText('src/webview')).toBeTruthy();
    expect(screen.getByText('/file.txt')).toBeTruthy();
    expect(screen.getByText('some/deeply/nested/path/to')).toBeTruthy();
  });

  it('marks the active row distinctly', () => {
    render(
      <FileFinderPanel
        paths={PATHS}
        activeIndex={1}
        onHover={() => undefined}
        onSelect={() => undefined}
      />
    );

    // eslint-disable-next-line testing-library/no-node-access
    const activeRow = screen.getByText('/App.tsx').closest('.file-finder-row');
    expect(activeRow?.className).toContain('file-finder-row-active');
    // eslint-disable-next-line testing-library/no-node-access
    const inactiveRow = screen.getByText('README.md').closest('.file-finder-row');
    expect(inactiveRow?.className).not.toContain('file-finder-row-active');
  });

  it('calls onSelect with the clicked row index', () => {
    const onSelect = vi.fn();
    render(
      <FileFinderPanel
        paths={PATHS}
        activeIndex={0}
        onHover={() => undefined}
        onSelect={onSelect}
      />
    );

    fireEvent.click(screen.getByText('README.md'));

    expect(onSelect).toHaveBeenCalledWith(0);
  });

  it('calls onHover with the hovered row index', () => {
    const onHover = vi.fn();
    render(
      <FileFinderPanel paths={PATHS} activeIndex={0} onHover={onHover} onSelect={() => undefined} />
    );

    fireEvent.mouseEnter(screen.getByText('/App.tsx'));

    expect(onHover).toHaveBeenCalledWith(1);
  });
});
