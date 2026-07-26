// (c) Copyright 2026 Aaron Kimball

import HistoryView, { type HistoryViewProps } from './components/HistoryView';
import {
  type HistoryEntryKind,
  type HistoryEntry,
  type PendingInteraction,
  type TextHistoryEntry,
  type ToolCallHistoryEntry,
  appendInteraction,
  appendPrompt,
  appendQuestionInteraction,
  applyExpandAllToolCalls,
  applyHostMessage,
  applyPendingInteraction,
  applyToolCallExpandedToggle,
  applyTurnFlag,
} from './historyModel';
import { type DiffRow, renderDiffLines } from './renderDiffLines';

export {
  type DiffRow,
  type HistoryEntry,
  type HistoryEntryKind,
  type HistoryViewProps,
  type PendingInteraction,
  type TextHistoryEntry,
  type ToolCallHistoryEntry,
  appendInteraction,
  appendPrompt,
  appendQuestionInteraction,
  applyExpandAllToolCalls,
  applyHostMessage,
  applyPendingInteraction,
  applyToolCallExpandedToggle,
  applyTurnFlag,
  renderDiffLines,
  HistoryView,
};
