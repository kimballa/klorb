// © Copyright 2026 Aaron Kimball
import SkillFinderPanel from './components/SkillFinderPanel';
import {
  buildSkillInsertion,
  detectSkillMention,
  splitSkillDisplay,
  type SkillFinderMatch,
  type SkillInsertion,
  type SkillMentionContext,
} from './skillFinderModel';
import useSkillFinder, { type SkillFinder, type SkillFinderSelection } from './useSkillFinder';

export {
  SkillFinderPanel,
  buildSkillInsertion,
  detectSkillMention,
  splitSkillDisplay,
  useSkillFinder,
  type SkillFinder,
  type SkillFinderMatch,
  type SkillFinderSelection,
  type SkillInsertion,
  type SkillMentionContext,
};

export type { Finder, FinderSelection } from '../fileFinder';
