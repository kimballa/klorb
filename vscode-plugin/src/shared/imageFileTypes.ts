// © Copyright 2026 Aaron Kimball
//
// Common data regarding image files

/** Extension -> MIME type for the status row's "Attach Image…" file picker (`_attachImageFile`)
 * -- the file-system equivalent of `PromptInput`'s `ACCEPTED_IMAGE_MIME_TYPES` drag/paste
 * filter, keyed by extension since a picked file's `type` isn't available the way a browser
 * `File`'s is. */
export const IMAGE_MIME_TYPE_BY_EXTENSION: Record<string, string> = {
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.png': 'image/png',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
  '.bmp': 'image/bmp',
  '.heic': 'image/heic',
  '.heif': 'image/heif',
};

/** The list of image file mime types we support. */
export const IMAGE_FILE_MIME_TYPES: string[] = Object.values(IMAGE_MIME_TYPE_BY_EXTENSION);

/** The list of image file extensions with leading '.' trimmed off. */
export const IMAGE_FILE_EXTENSIONS: string[] = Object.keys(IMAGE_MIME_TYPE_BY_EXTENSION).map(
  (ext) => ext.substring(1)
);
