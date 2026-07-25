// (c) Copyright 2026 Aaron Kimball

import HistoryView, { type HistoryViewProps } from './components/HistoryView';
import {
  type HistoryEntryKind,
  type HistoryEntry,
  type TextHistoryEntry,
  type ToolCallHistoryEntry,
  appendPrompt,
  applyExpandAllToolCalls,
  applyHostMessage,
  applyToolCallExpandedToggle,
  applyTurnFlag,
} from './historyModel';
import { type DiffRow, renderDiffLines } from './renderDiffLines';

export {
  type DiffRow,
  type HistoryEntry,
  type HistoryEntryKind,
  type HistoryViewProps,
  type TextHistoryEntry,
  type ToolCallHistoryEntry,
  appendPrompt,
  applyExpandAllToolCalls,
  applyHostMessage,
  applyToolCallExpandedToggle,
  applyTurnFlag,
  renderDiffLines,
  HistoryView,
};
