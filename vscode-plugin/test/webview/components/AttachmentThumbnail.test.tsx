/** @vitest-environment jsdom */
// © Copyright 2026 Aaron Kimball
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { AttachedImageMeta, ImageAttachment } from 'shared/webviewMessages';
import AttachmentThumbnail from 'webview/components/AttachmentThumbnail';

afterEach(cleanup);

const BASE_IMAGE: ImageAttachment = { mimeType: 'image/png', dataBase64: 'abcd' };

describe('AttachmentThumbnail', () => {
  it('captions a named attachment with its basename, stripping any leading directory parts', () => {
    render(<AttachmentThumbnail image={{ ...BASE_IMAGE, name: 'some/nested/dir/shot.png' }} />);
    expect(screen.getByAltText('shot.png')).toBeTruthy();
  });

  it('captions a nameless (clipboard) attachment as "(clipboard)"', () => {
    render(<AttachmentThumbnail image={BASE_IMAGE} />);
    expect(screen.getByAltText('(clipboard)')).toBeTruthy();
  });

  it('shows a dimensions caption when width/height are known', () => {
    render(<AttachmentThumbnail image={{ ...BASE_IMAGE, width: 123, height: 456 }} />);
    expect(screen.getByText('123x456')).toBeTruthy();
  });

  it('omits the dimensions caption when width/height are unknown', () => {
    render(<AttachmentThumbnail image={BASE_IMAGE} />);
    expect(screen.queryByText(/x\d+$/)).toBeNull();
  });

  it('renders a remove button only when onRemove is given, and dispatches it on click', () => {
    const onRemove = vi.fn();
    const { rerender } = render(<AttachmentThumbnail image={BASE_IMAGE} />);
    expect(screen.queryByTitle('Remove attachment')).toBeNull();

    rerender(<AttachmentThumbnail image={BASE_IMAGE} onRemove={onRemove} />);
    fireEvent.click(screen.getByTitle('Remove attachment'));

    expect(onRemove).toHaveBeenCalledOnce();
  });

  it('marks the thumbnail image non-draggable, so it cannot be dragged back onto the drop zone', () => {
    render(<AttachmentThumbnail image={BASE_IMAGE} />);
    expect(screen.getByAltText('(clipboard)').getAttribute('draggable')).toBe('false');
  });

  it('renders a paper-clip placeholder, not a broken <img>, when only metadata (no bytes) is given', () => {
    const meta: AttachedImageMeta = { name: 'shot.png', width: 123, height: 456 };
    render(<AttachmentThumbnail image={meta} />);

    expect(screen.queryByRole('img')).toBeNull();
    expect(screen.getByTitle('shot.png')).toBeTruthy();
    expect(screen.getByText('123x456')).toBeTruthy();
    expect(screen.getByText('shot.png')).toBeTruthy();
  });
});
