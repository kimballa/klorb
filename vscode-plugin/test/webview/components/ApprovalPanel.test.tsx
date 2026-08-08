/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { PermissionAskMessage } from 'shared/webviewMessages';
import ApprovalPanel, { type ApprovalDecision } from 'webview/components/ApprovalPanel';

afterEach(cleanup);

const BASH_ASK: PermissionAskMessage = {
  type: 'permissionAsk',
  requestId: 1,
  title: 'Run: ls -la /tmp',
  options: [
    { id: 'allow:once', name: 'Allow once', kind: 'allow_once', scope: 'once' },
    { id: 'deny:once', name: 'Deny', kind: 'reject_once', scope: 'once' },
    { id: 'allow:session', name: 'Allow for this session', kind: 'allow_always', scope: 'session' },
    { id: 'deny:session', name: 'Deny for this session', kind: 'reject_always', scope: 'session' },
    {
      id: 'allow:workspace',
      name: 'Always allow (workspace)',
      kind: 'allow_always',
      scope: 'workspace',
    },
    {
      id: 'allow:homedir',
      name: 'Always allow (home config)',
      kind: 'allow_always',
      scope: 'homedir',
    },
    {
      id: 'deny:workspace',
      name: 'Always deny (workspace)',
      kind: 'reject_always',
      scope: 'workspace',
    },
    {
      id: 'deny:homedir',
      name: 'Always deny (home config)',
      kind: 'reject_always',
      scope: 'homedir',
    },
  ],
  klorbMeta: {
    resourceDescription: 'run shell command: ls -la /tmp',
    headerKind: 'Run command',
    commandText: 'ls -la /tmp && echo done',
    itemCommandText: 'ls -la /tmp',
    itemIndex: 0,
    itemTotal: 2,
    grantPatterns: [['ls', '-la', '/tmp']],
    riskLevel: 2,
    riskRationale: 'Read-only directory listing.',
  },
};

const ESCALATION_ASK: PermissionAskMessage = {
  type: 'permissionAsk',
  requestId: 2,
  title: 'Escalate',
  options: [
    { id: 'allow:once', name: 'Approve for this session', kind: 'allow_once', scope: 'once' },
    { id: 'deny:once', name: 'Deny', kind: 'reject_once', scope: 'once' },
  ],
  klorbMeta: {
    escalation: {
      scope: 'homedir',
      description: 'Grant home-directory write access',
      reason: 'need to cache a large model file',
    },
  },
};

describe('ApprovalPanel', () => {
  it('renders the header, description, command preview, grants, risk, and rationale', () => {
    const { container } = render(<ApprovalPanel ask={BASH_ASK} onDecision={() => undefined} />);

    expect(screen.getByText('Permission requested: Run command')).toBeTruthy();
    expect(screen.getByText('1 of 2')).toBeTruthy();
    expect(screen.getByText('run shell command: ls -la /tmp')).toBeTruthy();
    expect(screen.getByText('ls -la /tmp')).toBeTruthy();
    expect(screen.getByText('grants: ls -la /tmp')).toBeTruthy();
    expect(screen.getByText('Risk: 2/10')).toBeTruthy();
    expect(screen.getByText('Read-only directory listing.')).toBeTruthy();
    // The full compound command is behind a collapsed disclosure, not rendered plain -- no
    // Testing Library query targets a bare <details> by class, so a direct query is the only
    // way to assert its collapsed state (mirrors App.test.tsx's thinking-disclosure check).
    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const disclosure = container.querySelector('.approval-command-full-disclosure');
    expect(disclosure?.hasAttribute('open')).toBe(false);
  });

  it('groups options into an Allow/Deny grid ordered by scope', () => {
    const { container } = render(<ApprovalPanel ask={BASH_ASK} onDecision={() => undefined} />);

    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    const headers = Array.from(container.querySelectorAll('.approval-options-header')).map(
      (header) => header.textContent
    );
    expect(headers).toEqual(['Allow', 'Deny']);
    for (const option of BASH_ASK.options) {
      const matches = screen.getAllByText(option.name);
      expect(matches.some((match) => match.tagName.toLowerCase() === 'vscode-button')).toBe(true);
    }
  });

  it('falls back to a flat option list when an option carries no scope', () => {
    const ask: PermissionAskMessage = {
      ...BASH_ASK,
      options: [{ id: 'allow:once', name: 'Allow once', kind: 'allow_once' }],
    };
    const { container } = render(<ApprovalPanel ask={ask} onDecision={() => undefined} />);

    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    expect(container.querySelector('.approval-options-grid')).toBeNull();
    expect(screen.getByText('Allow once')).toBeTruthy();
  });

  it('reveals the full command behind the "Show full command" disclosure', () => {
    render(<ApprovalPanel ask={BASH_ASK} onDecision={() => undefined} />);

    fireEvent.click(screen.getByText('Show full command'));

    expect(screen.getByText('ls -la /tmp && echo done')).toBeTruthy();
  });

  it('posts a selected decision when an option is clicked', () => {
    const onDecision = vi.fn<(decision: ApprovalDecision) => void>();
    render(<ApprovalPanel ask={BASH_ASK} onDecision={onDecision} />);

    fireEvent.click(screen.getByText('Allow for this session'));

    expect(onDecision).toHaveBeenCalledWith({ optionId: 'allow:session' });
  });

  it('posts a persistent-scope deny decision when a deny option is clicked', () => {
    const onDecision = vi.fn<(decision: ApprovalDecision) => void>();
    render(<ApprovalPanel ask={BASH_ASK} onDecision={onDecision} />);

    fireEvent.click(screen.getByText('Always deny (workspace)'));

    expect(onDecision).toHaveBeenCalledWith({ optionId: 'deny:workspace' });
  });

  it('posts cancelled on Escape', () => {
    const onDecision = vi.fn<(decision: ApprovalDecision) => void>();
    render(<ApprovalPanel ask={BASH_ASK} onDecision={onDecision} />);

    // Escape bubbles from any descendant up to the panel's own onKeyDown handler.
    fireEvent.keyDown(screen.getByText('Permission requested: Run command'), { key: 'Escape' });

    expect(onDecision).toHaveBeenCalledWith({ cancelled: true });
  });

  it('posts the deny-once option with otherText from the "Other…" field', () => {
    const onDecision = vi.fn<(decision: ApprovalDecision) => void>();
    render(<ApprovalPanel ask={BASH_ASK} onDecision={onDecision} />);

    fireEvent.click(screen.getByText('Other…'));
    // eslint-disable-next-line testing-library/no-node-access
    const textfield = document.querySelector('vscode-textfield') as HTMLElement & {
      value: string;
    };
    textfield.value = 'do something else instead';
    fireEvent(textfield, new Event('input', { bubbles: true }));
    fireEvent.click(screen.getByText('Send'));

    expect(onDecision).toHaveBeenCalledWith({
      optionId: 'deny:once',
      otherText: 'do something else instead',
    });
  });

  it('focuses the panel on mount so Escape works before the user clicks into it', () => {
    const { container } = render(<ApprovalPanel ask={BASH_ASK} onDecision={() => undefined} />);

    // eslint-disable-next-line testing-library/no-container, testing-library/no-node-access
    expect(document.activeElement).toBe(container.querySelector('.approval-panel'));
  });

  it('posts the deny-once option with otherText on Enter in the "Other…" field', () => {
    const onDecision = vi.fn<(decision: ApprovalDecision) => void>();
    render(<ApprovalPanel ask={BASH_ASK} onDecision={onDecision} />);

    fireEvent.click(screen.getByText('Other…'));
    // eslint-disable-next-line testing-library/no-node-access
    const textfield = document.querySelector('vscode-textfield') as HTMLElement & {
      value: string;
    };
    textfield.value = 'do something else instead';
    fireEvent(textfield, new Event('input', { bubbles: true }));
    fireEvent.keyDown(textfield, { key: 'Enter' });

    expect(onDecision).toHaveBeenCalledWith({
      optionId: 'deny:once',
      otherText: 'do something else instead',
    });
  });

  it('renders the escalation header, description, and reason distinctly', () => {
    render(<ApprovalPanel ask={ESCALATION_ASK} onDecision={() => undefined} />);

    expect(screen.getByText('Privilege escalation')).toBeTruthy();
    expect(screen.getByText('Grant home-directory write access')).toBeTruthy();
    expect(screen.getByText('Reason: need to cache a large model file')).toBeTruthy();
  });
});
