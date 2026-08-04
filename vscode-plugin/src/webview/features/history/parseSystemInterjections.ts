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
