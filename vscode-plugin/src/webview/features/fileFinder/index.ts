// © Copyright 2026 Aaron Kimball
import FileFinderPanel from './components/FileFinderPanel';
import {
  ancestorDirectories,
  buildDirectoryInsertion,
  buildMentionInsertion,
  detectMentionQuery,
  escapeMentionPath,
  pathDepth,
  splitFinderPath,
  splitQueryDirectory,
  type FinderMatch,
  type FinderPathParts,
  type FinderSelection,
  type MentionContext,
  type QueryDirectorySplit,
} from './fileFinderModel';
import {
  findMentionSpans,
  stripTrailingMentionPunctuation,
  TRAILING_MENTION_PUNCTUATION,
  unescapeMentionFilename,
  type MentionSpan,
} from './mentionParser';
import useFileFinder, { type Finder } from './useFileFinder';

export {
  type Finder,
  type FinderMatch,
  type FinderPathParts,
  type FinderSelection,
  type MentionContext,
  type MentionSpan,
  type QueryDirectorySplit,
  ancestorDirectories,
  buildDirectoryInsertion,
  buildMentionInsertion,
  detectMentionQuery,
  escapeMentionPath,
  findMentionSpans,
  pathDepth,
  splitFinderPath,
  splitQueryDirectory,
  stripTrailingMentionPunctuation,
  TRAILING_MENTION_PUNCTUATION,
  unescapeMentionFilename,
  FileFinderPanel,
  useFileFinder,
};
