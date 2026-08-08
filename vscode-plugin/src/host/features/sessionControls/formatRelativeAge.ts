// © Copyright 2026 Aaron Kimball

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

const MONTH_NAMES = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
];

function agoPhrase(count: number, unit: string): string {
  return `${count} ${unit}${count === 1 ? '' : 's'} ago`;
}

/** Formats `isoTimestamp` (a session's `updatedAt`) as an approximate age relative to `now`
 * (defaults to the current time; overridable for tests): "just now" under 2 minutes, "<n>
 * minutes/hours/day(s) ago" up through the last week, "last week"/"last month" for the next
 * couple of buckets, and a "<Month>, <year>" fallback beyond that -- drives the relative-age
 * text in the `klorb.browseSessions` QuickPick (see `browseSessionsCommand`). */
export function formatRelativeAge(isoTimestamp: string, now: Date = new Date()): string {
  const then = new Date(isoTimestamp);
  const diffMs = Math.max(0, now.getTime() - then.getTime());
  const diffMinutes = Math.floor(diffMs / MINUTE_MS);
  if (diffMinutes < 2) {
    return 'just now';
  }
  if (diffMinutes < 60) {
    return agoPhrase(diffMinutes, 'minute');
  }
  const diffHours = Math.floor(diffMs / HOUR_MS);
  if (diffHours < 24) {
    return agoPhrase(diffHours, 'hour');
  }
  const diffDays = Math.floor(diffMs / DAY_MS);
  if (diffDays < 7) {
    return agoPhrase(diffDays, 'day');
  }
  if (diffDays < 14) {
    return 'last week';
  }
  if (diffDays < 45) {
    return 'last month';
  }
  return `${MONTH_NAMES[then.getMonth()]}, ${then.getFullYear()}`;
}
