// © Copyright 2026 Aaron Kimball

const SI_SUFFIXES = ['', 'k', 'M', 'B'] as const;

/** Rounds `value` (>= 1) to 2 significant figures, e.g. `1.43 -> 1.4`, `423 -> 420` --
 * mirrors `klorb.tui.formatting._round_to_2_sig_figs`. */
function roundToTwoSigFigs(value: number): number {
  if (value < 10) {
    return Math.round(value * 10) / 10;
  }
  if (value < 100) {
    return Math.round(value);
  }
  return Math.round(value / 10) * 10;
}

/** Renders `count` using SI-style suffixes at 2 significant figures once >= 1000, e.g.
 * `1400 -> "1.4k"`, `423000 -> "420k"`. Counts under 1000 are shown as a literal integer.
 * Mirrors `klorb.tui.formatting.format_token_count`, the TUI status bar's own formatting. */
export function formatTokenCount(count: number): string {
  if (count < 1000) {
    return String(count);
  }

  let magnitude = 0;
  let value = count;
  while (value >= 1000 && magnitude < SI_SUFFIXES.length - 1) {
    value /= 1000;
    magnitude += 1;
  }

  value = roundToTwoSigFigs(value);
  if (value >= 1000 && magnitude < SI_SUFFIXES.length - 1) {
    value /= 1000;
    magnitude += 1;
  }

  return `${value}${SI_SUFFIXES[magnitude]}`;
}
