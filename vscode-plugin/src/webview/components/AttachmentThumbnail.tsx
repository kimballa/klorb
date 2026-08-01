// © Copyright 2026 Aaron Kimball
import type { JSX } from 'react';

import type { AttachedImageMeta, ImageAttachment } from 'shared/webviewMessages';

interface AttachmentThumbnailProps {
  image: ImageAttachment | AttachedImageMeta;
  /** Present only for a still-pending attachment (`PromptInput`'s tray) -- renders the remove
   * button. Omitted for an already-sent attachment in a history bubble (`HistoryView`), which
   * is read-only. */
  onRemove?: () => void;
}

function hasBytes(image: ImageAttachment | AttachedImageMeta): image is ImageAttachment {
  return 'dataBase64' in image;
}

/** One image thumbnail with its dimensions/filename caption below it -- shared by
 * `PromptInput`'s pending-attachment tray and `HistoryView`'s prompt bubbles, so an attached
 * image reads the same way whether it's still pending or already part of the sent history.
 * `image.name`'s display strips any leading directory parts (defensive: every current source
 * already hands over a bare filename, but this is the one place that renders it); a
 * clipboard-pasted attachment (no `name` at all) captions as "(clipboard)" instead of a blank
 * line. `draggable={false}` on the `<img>` keeps a user from dragging the thumbnail itself back
 * onto `PromptInput`'s own drop zone, which would otherwise re-trigger the drop handler and add
 * a duplicate attachment.
 *
 * `image` lacking `dataBase64` (an `AttachedImageMeta`, not a full `ImageAttachment`) means this
 * is a `_klorb/sessionReplay`-restored attachment: the server never resends an already-persisted
 * image's bytes just to redraw a thumbnail (see docs/specs/session-persistence.md), so a
 * paper-clip placeholder icon renders in place of the actual image, captioned with whatever
 * metadata *did* survive the round trip.
 */
export default function AttachmentThumbnail({
  image,
  onRemove,
}: AttachmentThumbnailProps): JSX.Element {
  const displayName =
    image.name !== undefined ? (image.name.split('/').pop() ?? image.name) : '(clipboard)';
  const dimensions =
    image.width !== undefined && image.height !== undefined
      ? `${image.width}x${image.height}`
      : undefined;

  return (
    <div className="attachment-thumb">
      <div className="attachment-thumb-image-wrap">
        {hasBytes(image) ? (
          <img
            src={`data:${image.mimeType};base64,${image.dataBase64}`}
            alt={displayName}
            title={displayName}
            draggable={false}
          />
        ) : (
          <div className="attachment-thumb-placeholder" title={displayName}>
            <vscode-icon name="attach" />
          </div>
        )}
      </div>
      {onRemove !== undefined ? (
        <button
          type="button"
          className="attachment-remove"
          title="Remove attachment"
          aria-label="Remove attachment"
          onClick={onRemove}>
          <vscode-icon name="close" />
        </button>
      ) : null}
      {dimensions !== undefined ? <div className="attachment-caption">{dimensions}</div> : null}
      <div className="attachment-caption">{displayName}</div>
    </div>
  );
}
