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
  type MentionContext,
  type MentionInsertion,
  type QueryDirectorySplit,
} from './fileFinderModel';
import {
  findMentionSpans,
  stripTrailingMentionPunctuation,
  TRAILING_MENTION_PUNCTUATION,
  unescapeMentionFilename,
  type MentionSpan,
} from './mentionParser';
import useFileFinder, { type FileFinder, type FileFinderSelection } from './useFileFinder';

export {
  type FileFinder,
  type FileFinderSelection,
  type FinderMatch,
  type FinderPathParts,
  type MentionContext,
  type MentionInsertion,
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
