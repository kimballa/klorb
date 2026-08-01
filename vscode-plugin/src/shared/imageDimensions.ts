// © Copyright 2026 Aaron Kimball

/**
 * Reads width/height directly from an image file's header bytes, for formats with a simple
 * fixed-offset or fixed-marker dimension field (PNG, GIF, BMP, JPEG) -- `undefined` for
 * anything else (WebP, HEIC/HEIF use more involved chunk formats) or malformed/too-short bytes.
 * Used by both the webview (a dropped/pasted attachment) and the extension host (the status
 * row's file picker) to caption an attachment's dimensions before it's ever sent to the server --
 * plain byte parsing rather than an async image decode, since neither side has a synchronous
 * "decode this and give me its size" primitive available (the webview's own jsdom test
 * environment never fires an `<img>`'s load event for a `data:` URI, and the extension host has
 * no DOM at all).
 */
export function readImageDimensions(
  bytes: Uint8Array
): { width: number; height: number } | undefined {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

  // PNG: 8-byte signature, then an IHDR chunk whose first 8 bytes are width/height (big-endian).
  if (bytes.length >= 24 && view.getUint32(0) === 0x89504e47 && view.getUint32(4) === 0x0d0a1a0a) {
    return { width: view.getUint32(16), height: view.getUint32(20) };
  }

  // GIF: 6-byte "GIF87a"/"GIF89a" signature, then width/height as little-endian uint16.
  if (bytes.length >= 10 && bytes[0] === 0x47 && bytes[1] === 0x49 && bytes[2] === 0x46) {
    return { width: view.getUint16(6, true), height: view.getUint16(8, true) };
  }

  // BMP: "BM" signature, then width/height as little-endian int32 in the DIB header.
  if (bytes.length >= 26 && bytes[0] === 0x42 && bytes[1] === 0x4d) {
    return { width: view.getInt32(18, true), height: Math.abs(view.getInt32(22, true)) };
  }

  // JPEG: scan markers for an SOFn (start-of-frame) segment; height/width follow its length field.
  if (bytes.length >= 4 && bytes[0] === 0xff && bytes[1] === 0xd8) {
    let offset = 2;
    while (offset + 9 < bytes.length && bytes[offset] === 0xff) {
      const marker = bytes[offset + 1]!;
      const isSofMarker =
        marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc;
      if (isSofMarker) {
        return { height: view.getUint16(offset + 5), width: view.getUint16(offset + 7) };
      }
      offset += 2 + view.getUint16(offset + 2);
    }
  }

  return undefined;
}
