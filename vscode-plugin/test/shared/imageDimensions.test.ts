// © Copyright 2026 Aaron Kimball
import { describe, expect, it } from 'vitest';

import { readImageDimensions } from 'shared/imageDimensions';

function pngBytes(width: number, height: number): Uint8Array {
  const bytes = new Uint8Array(24);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], 0);
  bytes.set([0x49, 0x48, 0x44, 0x52], 12); // "IHDR"
  const view = new DataView(bytes.buffer);
  view.setUint32(16, width);
  view.setUint32(20, height);
  return bytes;
}

function gifBytes(width: number, height: number): Uint8Array {
  const bytes = new Uint8Array(10);
  bytes.set([0x47, 0x49, 0x46, 0x38, 0x39, 0x61], 0); // "GIF89a"
  const view = new DataView(bytes.buffer);
  view.setUint16(6, width, true);
  view.setUint16(8, height, true);
  return bytes;
}

function bmpBytes(width: number, height: number): Uint8Array {
  const bytes = new Uint8Array(26);
  bytes.set([0x42, 0x4d], 0); // "BM"
  const view = new DataView(bytes.buffer);
  view.setInt32(18, width, true);
  view.setInt32(22, height, true);
  return bytes;
}

function jpegBytes(width: number, height: number): Uint8Array {
  const bytes = new Uint8Array(12);
  bytes.set([0xff, 0xd8, 0xff, 0xc0], 0); // SOI + SOF0 marker
  const view = new DataView(bytes.buffer);
  view.setUint16(4, 8); // segment length (unused by the parser, which returns before skipping)
  bytes[6] = 8; // sample precision
  view.setUint16(7, height);
  view.setUint16(9, width);
  return bytes;
}

describe('readImageDimensions', () => {
  it('reads PNG width/height from the IHDR chunk', () => {
    expect(readImageDimensions(pngBytes(123, 456))).toEqual({ width: 123, height: 456 });
  });

  it('reads GIF width/height (little-endian)', () => {
    expect(readImageDimensions(gifBytes(123, 456))).toEqual({ width: 123, height: 456 });
  });

  it('reads BMP width/height, taking the absolute value of a top-down (negative) height', () => {
    expect(readImageDimensions(bmpBytes(123, 456))).toEqual({ width: 123, height: 456 });
    expect(readImageDimensions(bmpBytes(123, -456))).toEqual({ width: 123, height: 456 });
  });

  it('reads JPEG width/height from an SOF0 marker segment', () => {
    expect(readImageDimensions(jpegBytes(123, 456))).toEqual({ width: 123, height: 456 });
  });

  it('returns undefined for an unrecognized format', () => {
    expect(readImageDimensions(new Uint8Array([0x52, 0x49, 0x46, 0x46]))).toBeUndefined();
  });

  it('returns undefined for empty or too-short bytes', () => {
    expect(readImageDimensions(new Uint8Array(0))).toBeUndefined();
    expect(readImageDimensions(new Uint8Array([0x89, 0x50]))).toBeUndefined();
  });
});
