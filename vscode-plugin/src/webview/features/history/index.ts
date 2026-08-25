// (c) Copyright 2026 Aaron Kimball

import BashToolCallChip from './components/BashToolCallChip';
import HistoryView, { type HistoryViewProps } from './components/HistoryView';
import MentionHighlightedText, {
  type MentionHighlightedTextProps,
} from './components/MentionHighlightedText';
import MessagingToolCallChip from './components/MessagingToolCallChip';
import SessionStatsCard from './components/SessionStatsCard';
import ToolCallChip from './components/ToolCallChip';
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
  ensureEntryId,
  isHistoryEntry,
  makeEntryId,
} from './historyModel';
import {
  type ParsedPromptWithInterjections,
  type ParsedSystemInterjection,
  parseSkillActivationIdentity,
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
  ensureEntryId,
  isHistoryEntry,
  makeEntryId,
  parseSkillActivationIdentity,
  parseSystemInterjections,
  renderDiffLines,
  renderYamlFrontmatter,
  BashToolCallChip,
  HistoryView,
  MentionHighlightedText,
  MessagingToolCallChip,
  SessionStatsCard,
  ToolCallChip,
};
