// © Copyright 2026 Aaron Kimball
import { Fragment, type JSX, type KeyboardEvent, useEffect, useRef, useState } from 'react';

import type { PermissionAskMessage, PermissionAskOption } from 'shared/webviewMessages';

/** The user's decision, handed up to the panel's owner (`App`) to post as a
 * `permissionDecision` webview message and record a compact history entry. */
export type ApprovalDecision = { optionId: string; otherText?: string } | { cancelled: true };

interface ApprovalPanelProps {
  ask: PermissionAskMessage;
  onDecision(decision: ApprovalDecision): void;
}

function metaString(klorbMeta: Record<string, unknown>, key: string): string | undefined {
  const value = klorbMeta[key];
  return typeof value === 'string' ? value : undefined;
}

function metaNumber(klorbMeta: Record<string, unknown>, key: string): number | undefined {
  const value = klorbMeta[key];
  return typeof value === 'number' ? value : undefined;
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((token) => typeof token === 'string');
}

function metaGrantPatterns(klorbMeta: Record<string, unknown>): string[][] | undefined {
  const value = klorbMeta.grantPatterns;
  return Array.isArray(value) && value.every(isStringArray) ? (value as string[][]) : undefined;
}

function metaEscalation(
  klorbMeta: Record<string, unknown>
): { scope?: string; description?: string } | undefined {
  const value = klorbMeta.escalation;
  return typeof value === 'object' && value !== null
    ? (value as { scope?: string; description?: string })
    : undefined;
}

function riskBand(riskLevel: number): 'low' | 'medium' | 'high' {
  if (riskLevel >= 7) {
    return 'high';
  }
  if (riskLevel >= 4) {
    return 'medium';
  }
  return 'low';
}

/** Row/column order the option grid groups by -- mirrors the TUI's `PermissionAskPanel` grid
 * (once/session/workspace/homedir rows, Allow/Deny columns). */
const SCOPE_ORDER = ['once', 'session', 'workspace', 'homedir'] as const;
type Scope = (typeof SCOPE_ORDER)[number];

interface ScopeRow {
  scope: Scope;
  allow?: PermissionAskOption;
  deny?: PermissionAskOption;
}

function isAllowOption(option: PermissionAskOption): boolean {
  return option.kind.startsWith('allow');
}

function isScope(value: string): value is Scope {
  return (SCOPE_ORDER as readonly string[]).includes(value);
}

/** One grid cell: `option`'s button, or an empty cell if this row has no option for this
 * column (e.g. a `StructuralResource` ask's rows have no workspace/homedir column at all, but
 * every row still renders both cells so the grid's columns stay aligned). */
function OptionCell({
  option,
  onDecision,
}: {
  option: PermissionAskOption | undefined;
  onDecision(decision: ApprovalDecision): void;
}): JSX.Element {
  if (option === undefined) {
    return <div className="approval-options-cell" />;
  }
  return (
    <div className="approval-options-cell">
      <vscode-button
        secondary={!isAllowOption(option)}
        onClick={() => onDecision({ optionId: option.id })}>
        {option.name}
      </vscode-button>
    </div>
  );
}

/** Groups `options` into one row per scope, each with its Allow/Deny cell, so the panel can
 * render the same allow/deny-by-scope hierarchy as the TUI's grid instead of a flat button list.
 * Returns `undefined` (falling back to a flat wrapping row) if any option is missing its own
 * `_meta.klorb.scope` or names a scope token this panel doesn't recognize -- e.g. a non-klorb ACP
 * agent, whose options this defensive fallback still renders, just without the grid grouping. */
function groupOptionsByScope(options: PermissionAskOption[]): ScopeRow[] | undefined {
  const rows = new Map<Scope, ScopeRow>();
  for (const option of options) {
    if (option.scope === undefined || !isScope(option.scope)) {
      return undefined;
    }
    const row = rows.get(option.scope) ?? { scope: option.scope };
    if (isAllowOption(option)) {
      row.allow = option;
    } else {
      row.deny = option;
    }
    rows.set(option.scope, row);
  }
  return SCOPE_ORDER.filter((scope) => rows.has(scope)).map((scope) => rows.get(scope) as ScopeRow);
}

/**
 * The approval panel mounted in the interaction area, directly above the prompt input, while a
 * `permissionAsk` is outstanding -- the tall-narrow equivalent of the TUI's interaction panel.
 * Renders a header naming the kind of access requested, a bash command preview with a "show full
 * command" disclosure, a grant-pattern summary, a risk badge with its rationale, the option grid
 * (grouped into Allow/Deny columns by scope, mirroring the TUI's own grid), and a free-text
 * "Other…" redirect. `klorbMeta.escalation` distinguishes an `EscalatePrivileges` ask with its
 * own header. Escape cancels (deny-once), matching the TUI's own Escape semantics. The panel
 * grabs focus on mount (and again each time a new ask replaces it) so that Escape reaches
 * `handleKeyDown` even before the user has clicked into an option or the "Other…" field -- a
 * plain `onKeyDown` only fires for keys pressed while focus is somewhere inside this subtree.
 */
export default function ApprovalPanel({ ask, onDecision }: ApprovalPanelProps): JSX.Element {
  const [otherText, setOtherText] = useState('');
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    panelRef.current?.focus();
  }, [ask.requestId]);

  const escalation = metaEscalation(ask.klorbMeta);
  const headerKind = metaString(ask.klorbMeta, 'headerKind');
  const resourceDescription = metaString(ask.klorbMeta, 'resourceDescription');
  const itemCommandText = metaString(ask.klorbMeta, 'itemCommandText');
  const commandText = metaString(ask.klorbMeta, 'commandText');
  const itemIndex = metaNumber(ask.klorbMeta, 'itemIndex');
  const itemTotal = metaNumber(ask.klorbMeta, 'itemTotal');
  const grantPatterns = metaGrantPatterns(ask.klorbMeta);
  const riskLevel = metaNumber(ask.klorbMeta, 'riskLevel');
  const riskRationale = metaString(ask.klorbMeta, 'riskRationale');
  const band = riskLevel !== undefined ? riskBand(riskLevel) : undefined;
  const scopeRows = groupOptionsByScope(ask.options);

  const headerText =
    escalation !== undefined
      ? 'Privilege escalation'
      : `Permission requested: ${headerKind ?? 'access'}`;

  function handleKeyDown(event: KeyboardEvent<HTMLElement>): void {
    if (event.key === 'Escape') {
      onDecision({ cancelled: true });
    }
  }

  function submitOther(): void {
    const text = otherText.trim();
    if (text.length === 0) {
      return;
    }
    const denyOnce = ask.options.find((option) => option.kind === 'reject_once') ?? ask.options[0];
    if (denyOnce === undefined) {
      return;
    }
    onDecision({ optionId: denyOnce.id, otherText: text });
  }

  function handleOtherKeyDown(event: KeyboardEvent<HTMLElement>): void {
    if (event.key === 'Enter') {
      submitOther();
    }
  }

  return (
    <div
      className={`approval-panel${escalation !== undefined ? ' approval-panel-escalation' : ''}`}
      tabIndex={-1}
      ref={panelRef}
      onKeyDown={handleKeyDown}>
      <div className="approval-header">
        {headerText}
        {itemIndex !== undefined && itemTotal !== undefined ? (
          <span className="approval-item-count">{`${itemIndex + 1} of ${itemTotal}`}</span>
        ) : null}
      </div>
      <div className="approval-body">
        {resourceDescription !== undefined ? (
          <div className="approval-description">{resourceDescription}</div>
        ) : null}
        {escalation?.description !== undefined ? (
          <div className="approval-description">{escalation.description}</div>
        ) : null}
        {itemCommandText !== undefined ? (
          <>
            <pre className="approval-command">{itemCommandText}</pre>
            {commandText !== undefined && commandText !== itemCommandText ? (
              <details className="approval-command-full-disclosure">
                <summary>Show full command</summary>
                <pre className="approval-command-full">{commandText}</pre>
              </details>
            ) : null}
          </>
        ) : null}
        {grantPatterns !== undefined && grantPatterns.length > 0 ? (
          <div className="approval-grants">
            {`grants: ${grantPatterns.map((pattern) => pattern.join(' ')).join(', ')}`}
          </div>
        ) : null}
        {riskLevel !== undefined ? (
          <div className={`approval-risk${band !== undefined ? ` approval-risk-${band}` : ''}`}>
            {`Risk: ${riskLevel}/10`}
          </div>
        ) : null}
        {riskRationale !== undefined ? (
          <div
            className={`approval-risk-rationale${
              band !== undefined ? ` approval-risk-${band}` : ''
            }`}>
            {riskRationale}
          </div>
        ) : null}
      </div>
      <div className="approval-options">
        {scopeRows !== undefined ? (
          <div className="approval-options-grid">
            <div className="approval-options-header">Allow</div>
            <div className="approval-options-header">Deny</div>
            {scopeRows.map((row) => (
              <Fragment key={row.scope}>
                <OptionCell option={row.allow} onDecision={onDecision} />
                <OptionCell option={row.deny} onDecision={onDecision} />
              </Fragment>
            ))}
          </div>
        ) : (
          ask.options.map((option) => (
            <vscode-button
              key={option.id}
              secondary={!isAllowOption(option)}
              onClick={() => onDecision({ optionId: option.id })}>
              {option.name}
            </vscode-button>
          ))
        )}
      </div>
      <details className="approval-other">
        <summary>Other…</summary>
        <div className="approval-other-row">
          <vscode-textfield
            placeholder="Redirect with a different instruction…"
            value={otherText}
            onInput={(event) => {
              const value = (event.target as { value?: unknown }).value;
              setOtherText(typeof value === 'string' ? value : '');
            }}
            onKeyDown={handleOtherKeyDown}
          />
          <vscode-button secondary onClick={submitOther}>
            Send
          </vscode-button>
        </div>
      </details>
    </div>
  );
}
