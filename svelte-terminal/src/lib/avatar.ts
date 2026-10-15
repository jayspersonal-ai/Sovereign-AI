// src/lib/avatar.ts
//
// Profile pictures are small square images stored inside the character card itself, in card.data.extensions.avatar,
// as a data URL. That way they sync between devices, go into backups and exports with the card, and need no
// extra file handling on the server.
//
// Anything stored there is treated as untrusted (a card can come from anywhere): `avatarFromExtensions` only
// returns plain raster image data URLs of a sensible size.

export const AVATAR_SIZE = 256; // pixels (square); shown much smaller, but this stays sharp on phone screens
export const MAX_SOURCE_BYTES = 20 * 1024 * 1024; // refuse huge files before trying to decode them
export const MAX_AVATAR_CHARS = 400_000; // stored picture limit (length of the data URL)

// SVG is excluded on purpose: raster formats only.
const AVATAR_RE = /^data:image\/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/]+={0,2}$/;

/** The picture stored in a card's `extensions`, if it is a safe image data URL; otherwise null. */
export function avatarFromExtensions(extensions: unknown): string | null {
  const value =
    extensions && typeof extensions === 'object' ? (extensions as Record<string, unknown>).avatar : undefined;
  return typeof value === 'string' && value.length <= MAX_AVATAR_CHARS && AVATAR_RE.test(value) ? value : null;
}

/** Same check for a picture kept somewhere other than a card (your own picture in the settings). */
export function safeAvatar(value: unknown): string | null {
  return typeof value === 'string' && value.length <= MAX_AVATAR_CHARS && AVATAR_RE.test(value) ? value : null;
}

/** The centred square to cut out of a width x height image. */
export function squareCrop(width: number, height: number): { sx: number; sy: number; side: number } {
  const side = Math.min(width, height);
  return { sx: Math.floor((width - side) / 2), sy: Math.floor((height - side) / 2), side };
}

interface Decoded {
  source: CanvasImageSource;
  width: number;
  height: number;
  release: () => void;
}

// Phone photos are often stored sideways with an "orientation" tag. Both routes below apply it.
async function decode(blob: Blob): Promise<Decoded> {
  if (typeof createImageBitmap === 'function') {
    try {
      const bitmap = await createImageBitmap(blob, { imageOrientation: 'from-image' });
      return { source: bitmap, width: bitmap.width, height: bitmap.height, release: () => bitmap.close() };
    } catch {
      /* older browsers: fall back to an <img> element */
    }
  }
  const url = URL.createObjectURL(blob);
  try {
    const img = new Image();
    img.decoding = 'async';
    img.src = url;
    await img.decode();
    return { source: img, width: img.naturalWidth, height: img.naturalHeight, release: () => URL.revokeObjectURL(url) };
  } catch {
    URL.revokeObjectURL(url);
    throw new Error('That file could not be read as an image.');
  }
}

/** Cut the centre square out of any image, shrink it to AVATAR_SIZE, and return it as a data URL. */
export async function blobToAvatar(blob: Blob, size: number = AVATAR_SIZE): Promise<string> {
  if (blob.type && !blob.type.startsWith('image/')) throw new Error('That file is not an image.');
  if (blob.size > MAX_SOURCE_BYTES) throw new Error('That image is too large (the limit is 20 MB).');

  const decoded = await decode(blob);
  try {
    if (!decoded.width || !decoded.height) throw new Error('That image has no content.');
    const { sx, sy, side } = squareCrop(decoded.width, decoded.height);

    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('This browser cannot process images.');
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(decoded.source, sx, sy, side, side, 0, 0, size, size);

    // WebP keeps transparency at a small size. A browser without WebP encoding silently returns PNG, which can be big.
    let out = canvas.toDataURL('image/webp', 0.88);
    if (!out.startsWith('data:image/webp') || out.length > MAX_AVATAR_CHARS) {
      // JPEG has no transparency, so put the picture on white first.
      ctx.globalCompositeOperation = 'destination-over';
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, size, size);
      out = canvas.toDataURL('image/jpeg', 0.88);
      if (out.length > MAX_AVATAR_CHARS) out = canvas.toDataURL('image/jpeg', 0.7);
    }
    if (out.length > MAX_AVATAR_CHARS || !AVATAR_RE.test(out)) throw new Error('That image could not be made small enough.');
    return out;
  } finally {
    decoded.release();
  }
}

/** Load an image from an address (the workspace panel's images, for example) and make an avatar from it. */
export async function urlToAvatar(url: string, size: number = AVATAR_SIZE): Promise<string> {
  let response: Response;
  try {
    response = await fetch(url, { credentials: 'same-origin' });
  } catch {
    throw new Error("The browser couldn't read that image. It may be on another server that doesn't allow it.");
  }
  if (!response.ok) throw new Error(`Couldn't load that image (error ${response.status}).`);
  return blobToAvatar(await response.blob(), size);
}
