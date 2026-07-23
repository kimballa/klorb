// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import { classifyEnterKey } from '../src/webview/keyHandling';

describe('classifyEnterKey', () => {
  it('submits on a bare Enter', () => {
    expect(classifyEnterKey(false, false)).toBe('submit');
  });

  it('inserts a newline on Shift+Enter', () => {
    expect(classifyEnterKey(true, false)).toBe('newline');
  });
});
