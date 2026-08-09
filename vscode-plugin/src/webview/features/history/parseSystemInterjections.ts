// © Copyright 2026 Aaron Kimball

/** A single `<SystemInterjection subject="...">...</SystemInterjection>` block parsed from the
 * leading position of a user message. */
export interface ParsedSystemInterjection {
  subject: string;
  body: string;
}

/** Parsed result: zero or more leading `SystemInterjection` blocks plus the remaining text (with
 * the interjections and surrounding whitespace elided). */
export interface ParsedPromptWithInterjections {
  interjections: ParsedSystemInterjection[];
  remainingText: string;
}

/** Matches one `<SystemInterjection subject="...">` block (opening tag, body, closing tag) at the
 * leading edge of a string, consuming trailing newlines. Repeated application peels off stacked
 * interjections. */
const INTERJECTION_RE = /^<SystemInterjection\s+subject="([^"]*)">\n/;

const CLOSING_TAG = '</SystemInterjection>';

/** Parse leading `<SystemInterjection>` blocks from `text`, returning each interjection's
 * `subject` and `body` plus the remaining text after all interjections and their surrounding
 * whitespace have been stripped. SystemInterjection blocks do not nest. */
export function parseSystemInterjections(text: string): ParsedPromptWithInterjections {
  const interjections: ParsedSystemInterjection[] = [];
  let remaining = text;

  while (true) {
    const match = INTERJECTION_RE.exec(remaining);
    if (match === null) {
      break;
    }
    const subject: string = match[1] ?? '';
    const afterOpening = remaining.slice(match[0].length);
    const closeIndex = afterOpening.indexOf(CLOSING_TAG);
    if (closeIndex === -1) {
      break;
    }
    const body = afterOpening.slice(0, closeIndex);
    const afterClosing = afterOpening.slice(closeIndex + CLOSING_TAG.length);
    interjections.push({ subject, body });
    // Strip the closing tag's trailing newline (if any) and any further leading whitespace.
    remaining = afterClosing.replace(/^\n?/, '');
  }

  if (interjections.length === 0) {
    return { interjections: [], remainingText: text };
  }

  return {
    interjections,
    remainingText: remaining.trimStart(),
  };
}

/** A `UserSkillActivation` interjection's body is an explanatory sentence followed by the
 * skill's JSON payload (`{namespace, name, content, files, tokens}` -- see
 * `klorb.tools.skill.common.skill_activation_payload`) on the line(s) after it. Extracts just
 * `namespace`/`name` for a friendly "Activated skill: <namespace>/<name>" label, without needing
 * the full payload (its `content` can be large). Returns `undefined` if `body` doesn't parse. */
export function parseSkillActivationIdentity(
  body: string
): { namespace: string; name: string } | undefined {
  const jsonStart = body.indexOf('{');
  if (jsonStart === -1) {
    return undefined;
  }
  try {
    const payload = JSON.parse(body.slice(jsonStart)) as { namespace?: unknown; name?: unknown };
    if (typeof payload.namespace === 'string' && typeof payload.name === 'string') {
      return { namespace: payload.namespace, name: payload.name };
    }
    return undefined;
  } catch {
    return undefined;
  }
}
