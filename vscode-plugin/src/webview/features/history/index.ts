// (c) Copyright 2026 Aaron Kimball

import HistoryView, { type HistoryViewProps } from './components/HistoryView';
import MentionHighlightedText, {
  type MentionHighlightedTextProps,
} from './components/MentionHighlightedText';
import SessionStatsCard from './components/SessionStatsCard';
import {
  type HistoryEntryKind,
  type HistoryEntry,
  type PendingInteraction,
  type SessionStatsHistoryEntry,
  type TaskListSnapshot,
  type TextHistoryEntry,
  type ToolCallHistoryEntry,
  appendInteraction,
  appendPrompt,
  appendQuestionInteraction,
  appendQueuedMessage,
  appendToolCallLimitInteraction,
  applyHostMessage,
  applyInterruptedMarker,
  applyPendingInteraction,
  applyQueuedMessageSent,
  applySessionReplay,
  applyTaskListUpdate,
  applyToolCallExpandedToggle,
  applyTurnFlag,
  isHistoryEntry,
  isScrollPinnedToBottom,
} from './historyModel';
import {
  type ParsedPromptWithInterjections,
  type ParsedSystemInterjection,
  parseSystemInterjections,
} from './parseSystemInterjections';
import { type DiffRow, renderDiffLines } from './renderDiffLines';
import { renderYamlFrontmatter } from './renderYamlFrontmatter';

export {
  type DiffRow,
  type HistoryEntry,
  type HistoryEntryKind,
  type HistoryViewProps,
  type MentionHighlightedTextProps,
  type ParsedPromptWithInterjections,
  type ParsedSystemInterjection,
  type PendingInteraction,
  type SessionStatsHistoryEntry,
  type TaskListSnapshot,
  type TextHistoryEntry,
  type ToolCallHistoryEntry,
  appendInteraction,
  appendPrompt,
  appendQuestionInteraction,
  appendQueuedMessage,
  appendToolCallLimitInteraction,
  applyHostMessage,
  applyInterruptedMarker,
  applyPendingInteraction,
  applyQueuedMessageSent,
  applySessionReplay,
  applyTaskListUpdate,
  applyToolCallExpandedToggle,
  applyTurnFlag,
  isHistoryEntry,
  isScrollPinnedToBottom,
  parseSystemInterjections,
  renderDiffLines,
  renderYamlFrontmatter,
  HistoryView,
  MentionHighlightedText,
  SessionStatsCard,
};
