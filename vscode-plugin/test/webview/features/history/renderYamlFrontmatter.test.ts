// © Copyright 2026 Aaron Kimball
import type { Element } from 'hast';
import type { Yaml } from 'mdast';
import type { State } from 'mdast-util-to-hast';
import { describe, expect, it } from 'vitest';

import { renderYamlFrontmatter } from 'webview/features/history';

const fakeState = {} as State;

function yamlNode(value: string): Yaml {
  return { type: 'yaml', value };
}

/** Narrows a hast element's child at `index` to an `Element`, for drilling into a rendered
 * frontmatter tree in tests that only care about elements, never `Text` nodes, at that depth. */
function elementChild(element: Element, index: number): Element {
  const child = element.children[index];
  if (child === undefined || child.type !== 'element') {
    throw new Error(`expected an element child at index ${index}, got ${JSON.stringify(child)}`);
  }
  return child;
}

describe('renderYamlFrontmatter', () => {
  it('renders a flat mapping as a two-column key/value table', () => {
    // eslint-disable-next-line testing-library/render-result-naming-convention -- pure function, not @testing-library/react's render()
    const result = renderYamlFrontmatter(
      fakeState,
      yamlNode('title: Hello\ncount: 3\n'),
      undefined
    );
    expect(result).toEqual({
      type: 'element',
      tagName: 'table',
      properties: { className: ['frontmatter-table'] },
      children: [
        {
          type: 'element',
          tagName: 'tr',
          properties: {},
          children: [
            {
              type: 'element',
              tagName: 'td',
              properties: { className: ['frontmatter-key'] },
              children: [{ type: 'text', value: 'title' }],
            },
            {
              type: 'element',
              tagName: 'td',
              properties: { className: ['frontmatter-value'] },
              children: [{ type: 'text', value: 'Hello' }],
            },
          ],
        },
        {
          type: 'element',
          tagName: 'tr',
          properties: {},
          children: [
            {
              type: 'element',
              tagName: 'td',
              properties: { className: ['frontmatter-key'] },
              children: [{ type: 'text', value: 'count' }],
            },
            {
              type: 'element',
              tagName: 'td',
              properties: { className: ['frontmatter-value'] },
              children: [{ type: 'text', value: '3' }],
            },
          ],
        },
      ],
    });
  });

  it('renders a nested mapping as a nested two-column table', () => {
    // eslint-disable-next-line testing-library/render-result-naming-convention -- pure function, not @testing-library/react's render()
    const result = renderYamlFrontmatter(
      fakeState,
      yamlNode('author:\n  name: Aaron\n  role: dev\n'),
      undefined
    );
    const valueCell = elementChild(elementChild(result as Element, 0), 1);
    expect(valueCell.children[0]).toEqual({
      type: 'element',
      tagName: 'table',
      properties: { className: ['frontmatter-table'] },
      children: [
        {
          type: 'element',
          tagName: 'tr',
          properties: {},
          children: [
            {
              type: 'element',
              tagName: 'td',
              properties: { className: ['frontmatter-key'] },
              children: [{ type: 'text', value: 'name' }],
            },
            {
              type: 'element',
              tagName: 'td',
              properties: { className: ['frontmatter-value'] },
              children: [{ type: 'text', value: 'Aaron' }],
            },
          ],
        },
        {
          type: 'element',
          tagName: 'tr',
          properties: {},
          children: [
            {
              type: 'element',
              tagName: 'td',
              properties: { className: ['frontmatter-key'] },
              children: [{ type: 'text', value: 'role' }],
            },
            {
              type: 'element',
              tagName: 'td',
              properties: { className: ['frontmatter-value'] },
              children: [{ type: 'text', value: 'dev' }],
            },
          ],
        },
      ],
    });
  });

  it('renders an array value as one stacked div per element', () => {
    // eslint-disable-next-line testing-library/render-result-naming-convention -- pure function, not @testing-library/react's render()
    const result = renderYamlFrontmatter(fakeState, yamlNode('tags:\n  - a\n  - b\n'), undefined);
    const valueCell = elementChild(elementChild(result as Element, 0), 1);
    expect(valueCell.children[0]).toEqual({
      type: 'element',
      tagName: 'div',
      properties: { className: ['frontmatter-array'] },
      children: [
        {
          type: 'element',
          tagName: 'div',
          properties: { className: ['frontmatter-array-item'] },
          children: [{ type: 'text', value: 'a' }],
        },
        {
          type: 'element',
          tagName: 'div',
          properties: { className: ['frontmatter-array-item'] },
          children: [{ type: 'text', value: 'b' }],
        },
      ],
    });
  });

  it('returns undefined for unparseable YAML instead of throwing', () => {
    expect(renderYamlFrontmatter(fakeState, yamlNode('{a: 1'), undefined)).toBeUndefined();
  });
});
