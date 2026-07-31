// © Copyright 2026 Aaron Kimball
import FileFinderPanel from './components/FileFinderPanel';
import {
  buildMentionInsertion,
  detectMentionQuery,
  escapeMentionPath,
  splitFinderPath,
  type FinderPathParts,
  type MentionContext,
  type MentionInsertion,
} from './fileFinderModel';
import useFileFinder, { type FileFinder, type FileFinderSelection } from './useFileFinder';

export {
  type FileFinder,
  type FileFinderSelection,
  type FinderPathParts,
  type MentionContext,
  type MentionInsertion,
  buildMentionInsertion,
  detectMentionQuery,
  escapeMentionPath,
  splitFinderPath,
  FileFinderPanel,
  useFileFinder,
};
