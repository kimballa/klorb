// © Copyright 2026 Aaron Kimball
import { type JSX, useEffect, useRef } from 'react';

import type { ChatMentionMatch } from '../chatMentionFinderModel';

interface ChatFinderPanelProps {
  matches: ChatMentionMatch[];
  activeIndex: number;
  onHover(index: number): void;
  onSelect(index: number): void;
}

/** The chat room's `@`-mention finder popup, styled via `FileFinderPanel`/`SkillFinderPanel`'s own
 * `.file-finder-panel`/`.file-finder-row` classes. */
export default function ChatFinderPanel({
  matches,
  activeIndex,
  onHover,
  onSelect,
}: ChatFinderPanelProps): JSX.Element {
  const activeRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex]);

  return (
    <div className="file-finder-panel">
      {matches.map((match, index) => {
        const active = index === activeIndex;
        return (
          <div
            key={match.node.id}
            ref={active ? activeRef : undefined}
            className={`file-finder-row${active ? ' file-finder-row-active' : ''}`}
            onMouseEnter={() => onHover(index)}
            onClick={() => onSelect(index)}>
            <span className="file-finder-row-file">@{match.nickname}</span>
            {match.node.title !== null ? (
              <span className="skill-finder-row-desc">{match.node.title}</span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
