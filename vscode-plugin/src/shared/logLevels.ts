// © Copyright 2026 Aaron Kimball

/** Python `logging` module numeric level value for `DEBUG`. */
export const DEBUG_LEVEL_VALUE = 10;
/** Python `logging` module numeric level value for `INFO`. */
export const INFO_LEVEL_VALUE = 20;
/** Python `logging` module numeric level value for `WARNING`. */
export const WARNING_LEVEL_VALUE = 30;
/** Python `logging` module numeric level value for `ERROR`. */
export const ERROR_LEVEL_VALUE = 40;
/** Python `logging` module numeric level value for `CRITICAL`. */
export const CRITICAL_LEVEL_VALUE = 50;

const LOG_LEVEL_VALUES: Readonly<Record<string, number>> = {
  DEBUG: DEBUG_LEVEL_VALUE,
  INFO: INFO_LEVEL_VALUE,
  WARNING: WARNING_LEVEL_VALUE,
  ERROR: ERROR_LEVEL_VALUE,
  CRITICAL: CRITICAL_LEVEL_VALUE,
};

/** Maps a Python `logging` module level name to its numeric value, or `0` for an unrecognized
 * name. */
export function logLevelValue(name: string): number {
  return LOG_LEVEL_VALUES[name] ?? 0;
}
