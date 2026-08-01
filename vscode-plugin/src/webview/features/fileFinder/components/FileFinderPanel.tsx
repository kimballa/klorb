// © Copyright 2026 Aaron Kimball
import { type JSX, useEffect, useRef } from 'react';

import { type FinderMatch, splitFinderPath } from '../fileFinderModel';

interface FileFinderPanelProps {
  matches: FinderMatch[];
  activeIndex: number;
  onHover(index: number): void;
  onSelect(index: number): void;
}

/**
 * The `@`-mention file finder popup, anchored just above the prompt input by `PromptInput`'s own
 * CSS (`.input-row` is `position: relative`, this panel is `position: absolute; bottom: 100%`) --
 * visually in the same family as `ApprovalPanel`/`QuestionPanel` but not part of the
 * `#interaction-area` protocol-driven flow, since it's purely local textarea UI state. Each row
 * splits its path into a truncatable directory part and a fixed file part (`splitFinderPath`),
 * with a trailing `/` appended to the file part for a directory match, so a deeply nested path
 * reads as ".../path/to/file.txt" instead of wrapping or scrolling horizontally. Keeps the
 * active row scrolled into view as `activeIndex` changes via keyboard navigation.
 */
export default function FileFinderPanel({
  matches,
  activeIndex,
  onHover,
  onSelect,
}: FileFinderPanelProps): JSX.Element {
  const activeRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex]);

  return (
    <div className="file-finder-panel">
      {matches.map((match, index) => {
        const { dirPart, filePart } = splitFinderPath(match.path);
        const displayFilePart = match.isDir ? `${filePart}/` : filePart;
        const active = index === activeIndex;
        return (
          <div
            key={match.path}
            ref={active ? activeRef : undefined}
            className={`file-finder-row${active ? ' file-finder-row-active' : ''}`}
            onMouseEnter={() => onHover(index)}
            onClick={() => onSelect(index)}>
            {dirPart.length > 0 ? <span className="file-finder-row-dir">{dirPart}</span> : null}
            <span className="file-finder-row-file">{displayFilePart}</span>
          </div>
        );
      })}
    </div>
  );
}
