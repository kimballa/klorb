// © Copyright 2026 Aaron Kimball
import type { Element, ElementContent, Text } from 'hast';
import type { Yaml } from 'mdast';
import type { Handler, State } from 'mdast-util-to-hast';
import { parse as parseYaml } from 'yaml';

function textNode(value: unknown): Text {
  return { type: 'text', value: value === null || value === undefined ? '' : String(value) };
}

function renderArray(items: readonly unknown[]): Element {
  return {
    type: 'element',
    tagName: 'div',
    properties: { className: ['frontmatter-array'] },
    children: items.map((item): Element => ({
      type: 'element',
      tagName: 'div',
      properties: { className: ['frontmatter-array-item'] },
      children: [renderFrontmatterValue(item)],
    })),
  };
}

function renderTable(entries: Readonly<Record<string, unknown>>): Element {
  return {
    type: 'element',
    tagName: 'table',
    properties: { className: ['frontmatter-table'] },
    children: Object.entries(entries).map(([key, value]): Element => ({
      type: 'element',
      tagName: 'tr',
      properties: {},
      children: [
        {
          type: 'element',
          tagName: 'td',
          properties: { className: ['frontmatter-key'] },
          children: [textNode(key)],
        },
        {
          type: 'element',
          tagName: 'td',
          properties: { className: ['frontmatter-value'] },
          children: [renderFrontmatterValue(value)],
        },
      ],
    })),
  };
}

/** Renders one parsed YAML value as hast content: a plain object becomes a nested two-column
 * key/value `<table>`, an array becomes one stacked `<div>` per element, and anything else
 * renders as plain text. */
function renderFrontmatterValue(value: unknown): ElementContent {
  if (Array.isArray(value)) {
    return renderArray(value);
  }
  if (value !== null && typeof value === 'object') {
    return renderTable(value as Record<string, unknown>);
  }
  return textNode(value);
}

/**
 * `remark-rehype` handler for the `yaml` mdast node `remark-frontmatter` produces from a
 * response's leading `---`-delimited block. `mdast-util-to-hast` drops `yaml` nodes by default;
 * this instead parses the block's raw text with the `yaml` package and renders it as a
 * two-column key/value table (nested objects as nested tables, arrays as stacked divs -- see
 * `renderFrontmatterValue`), falling back to omitting the block if it doesn't parse as YAML.
 */
export const renderYamlFrontmatter: Handler = (
  _state: State,
  node: Yaml
): ElementContent | undefined => {
  let parsed: unknown;
  try {
    parsed = parseYaml(node.value);
  } catch {
    return undefined;
  }
  return renderFrontmatterValue(parsed);
};
