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
import useFileFinder, { type FileFinder, type FileFinderSelection } from './useFileFinder';

export {
  type FileFinder,
  type FileFinderSelection,
  type FinderMatch,
  type FinderPathParts,
  type MentionContext,
  type MentionInsertion,
  type QueryDirectorySplit,
  ancestorDirectories,
  buildDirectoryInsertion,
  buildMentionInsertion,
  detectMentionQuery,
  escapeMentionPath,
  pathDepth,
  splitFinderPath,
  splitQueryDirectory,
  FileFinderPanel,
  useFileFinder,
};
