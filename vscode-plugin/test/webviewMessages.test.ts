// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import {
  parseHostMessage,
  parseWebviewMessage,
  type HostMessage,
  type WebviewMessage,
} from '../src/shared/webviewMessages';

describe('parseHostMessage', () => {
  it('round-trips every host message shape', () => {
    const messages: HostMessage[] = [
      { type: 'turnStarted' },
      { type: 'agentChunk', text: 'hello' },
      { type: 'thoughtChunk', text: 'hmm' },
      { type: 'turnEnded', stopReason: 'end_turn' },
      { type: 'turnError', message: 'boom' },
      { type: 'sessionReset' },
    ];
    for (const message of messages) {
      expect(parseHostMessage(message)).toEqual(message);
    }
  });

  it('rejects unknown types and malformed payloads', () => {
    expect(parseHostMessage(undefined)).toBeUndefined();
    expect(parseHostMessage(null)).toBeUndefined();
    expect(parseHostMessage('turnStarted')).toBeUndefined();
    expect(parseHostMessage({ type: 'reply', text: 'legacy shape' })).toBeUndefined();
    expect(parseHostMessage({ type: 'agentChunk' })).toBeUndefined();
    expect(parseHostMessage({ type: 'agentChunk', text: 42 })).toBeUndefined();
    expect(parseHostMessage({ type: 'turnEnded' })).toBeUndefined();
    expect(parseHostMessage({ type: 'turnError', message: null })).toBeUndefined();
    expect(parseHostMessage({ type: 'submitPrompt', text: 'wrong direction' })).toBeUndefined();
  });
});

describe('parseWebviewMessage', () => {
  it('round-trips every webview message shape', () => {
    const messages: WebviewMessage[] = [
      { type: 'submitPrompt', text: 'do the thing' },
      { type: 'cancelTurn' },
    ];
    for (const message of messages) {
      expect(parseWebviewMessage(message)).toEqual(message);
    }
  });

  it('rejects unknown types and malformed payloads', () => {
    expect(parseWebviewMessage(undefined)).toBeUndefined();
    expect(parseWebviewMessage({ type: 'submit', text: 'legacy shape' })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'submitPrompt' })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'submitPrompt', text: 7 })).toBeUndefined();
    expect(parseWebviewMessage({ type: 'agentChunk', text: 'wrong direction' })).toBeUndefined();
  });
});
