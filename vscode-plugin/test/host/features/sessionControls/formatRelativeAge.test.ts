// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import { formatRelativeAge } from 'host/features/sessionControls';

const NOW = new Date('2026-08-07T12:00:00.000Z');

function minutesAgo(minutes: number): string {
  return new Date(NOW.getTime() - minutes * 60_000).toISOString();
}

function hoursAgo(hours: number): string {
  return minutesAgo(hours * 60);
}

function daysAgo(days: number): string {
  return hoursAgo(days * 24);
}

describe('formatRelativeAge', () => {
  it('reports "just now" for anything under 2 minutes old', () => {
    expect(formatRelativeAge(minutesAgo(0), NOW)).toBe('just now');
    expect(formatRelativeAge(minutesAgo(1), NOW)).toBe('just now');
  });

  it('reports minutes ago within the last hour', () => {
    expect(formatRelativeAge(minutesAgo(2), NOW)).toBe('2 minutes ago');
    expect(formatRelativeAge(minutesAgo(59), NOW)).toBe('59 minutes ago');
  });

  it('reports hours ago within the last day', () => {
    expect(formatRelativeAge(hoursAgo(1), NOW)).toBe('1 hour ago');
    expect(formatRelativeAge(hoursAgo(23), NOW)).toBe('23 hours ago');
  });

  it('reports day(s) ago within the last week', () => {
    expect(formatRelativeAge(daysAgo(1), NOW)).toBe('1 day ago');
    expect(formatRelativeAge(daysAgo(6), NOW)).toBe('6 days ago');
  });

  it('reports "last week" for 7-13 days old', () => {
    expect(formatRelativeAge(daysAgo(7), NOW)).toBe('last week');
    expect(formatRelativeAge(daysAgo(13), NOW)).toBe('last week');
  });

  it('reports "last month" for 14-44 days old', () => {
    expect(formatRelativeAge(daysAgo(14), NOW)).toBe('last month');
    expect(formatRelativeAge(daysAgo(44), NOW)).toBe('last month');
  });

  it('falls back to "<Month>, <year>" beyond that', () => {
    const oldTimestamp = new Date('2025-04-15T00:00:00.000Z').toISOString();
    expect(formatRelativeAge(oldTimestamp, NOW)).toBe('April, 2025');
  });

  it('clamps a future timestamp to "just now" instead of a negative age', () => {
    expect(formatRelativeAge(minutesAgo(-5), NOW)).toBe('just now');
  });
});
