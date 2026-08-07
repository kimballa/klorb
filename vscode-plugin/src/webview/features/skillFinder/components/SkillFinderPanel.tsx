// © Copyright 2026 Aaron Kimball
import { type JSX, useEffect, useRef } from 'react';

import { splitSkillDisplay, type SkillFinderMatch } from '../skillFinderModel';

interface SkillFinderPanelProps {
  matches: SkillFinderMatch[];
  activeIndex: number;
  onHover(index: number): void;
  onSelect(index: number): void;
}

export default function SkillFinderPanel({
  matches,
  activeIndex,
  onHover,
  onSelect,
}: SkillFinderPanelProps): JSX.Element {
  const activeRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex]);

  return (
    <div className="file-finder-panel">
      {matches.map((match, index) => {
        const { name, desc } = splitSkillDisplay(match.skill);
        const active = index === activeIndex;
        return (
          <div
            key={match.skill.name}
            ref={active ? activeRef : undefined}
            className={`file-finder-row${active ? ' file-finder-row-active' : ''}`}
            onMouseEnter={() => onHover(index)}
            onClick={() => onSelect(index)}>
            <span className="file-finder-row-file">/{name}</span>
            {desc.length > 0 ? <span className="skill-finder-row-desc">{desc}</span> : null}
          </div>
        );
      })}
    </div>
  );
}
