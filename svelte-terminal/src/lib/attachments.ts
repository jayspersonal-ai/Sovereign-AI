// src/lib/attachments.ts
//
// Images the user pastes, drops or attaches in the chat.
//
// An image goes to the AI two ways at once: it is saved in the AI's workspace folder (so it persists, shows in the
// workspace panel, and the AI can open it again later by path), and it is sent inline with the message (so the AI
// sees it right away). The message text saved in the database carries a one-line marker with the saved path, which is
// how the chat shows the thumbnail again after a reload.

import type { ChatAttachment } from './types';

/** What one image costs the model's context (your ViT setting). Used for the estimate until the server reports the exact size. */
export const IMAGE_TOKEN_ESTIMATE = 1120;

const MARKER = /^\[Attached image: (.+?)\]\s*$/gm;

export function attachmentLine(path: string): string {
  return `[Attached image: ${path}]`;
}

/** The message text with the marker lines removed, plus the image paths those lines named. */
export function splitAttachments(content: string): { text: string; paths: string[] } {
  const paths: string[] = [];
  const text = (content ?? '').replace(MARKER, (_m, p: string) => {
    paths.push(p);
    return '';
  });
  return { text: text.replace(/\n{3,}/g, '\n\n').trim(), paths };
}

/** The address the browser uses to show a saved workspace image. */
export function workspaceImageUrl(path: string): string {
  return '/v1/workspace/file?path=' + encodeURIComponent(path).replace(/%2F/g, '/');
}

/** Message text as saved: what was typed, then one marker line per image. */
export function composeWithAttachments(text: string, attachments: ChatAttachment[]): string {
  const lines = attachments.map((a) => attachmentLine(a.path));
  return [text.trim(), ...lines].filter(Boolean).join('\n\n');
}

/** The message in the shape the model API takes: text plus the images inline. */
export function toModelContent(content: string, inlineImages?: string[]): string | Array<Record<string, unknown>> {
  if (!inlineImages?.length) return content;
  return [
    { type: 'text', text: content },
    ...inlineImages.map((url) => ({ type: 'image_url', image_url: { url } }))
  ];
}

export function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error ?? new Error('Could not read the image.'));
    r.readAsDataURL(blob);
  });
}

const KEEP_BELOW_BYTES = 1_500_000;
const KEEP_BELOW_PIXELS = 2048;
const SHRINK_TO_PIXELS = 1600;

/**
 * Big photos are shrunk before upload (kind to a weak signal, and models don't need more detail than this).
 * Small images are left exactly as they are. If the browser can't decode the image, it is sent untouched
 * and the server says whether it accepts the type.
 */
export async function prepareImage(file: Blob): Promise<Blob> {
  try {
    if (typeof createImageBitmap !== 'function') return file;
    const bmp = await createImageBitmap(file);
    const longest = Math.max(bmp.width, bmp.height);
    if (file.size <= KEEP_BELOW_BYTES && longest <= KEEP_BELOW_PIXELS) {
      bmp.close?.();
      return file;
    }
    const scale = Math.min(1, SHRINK_TO_PIXELS / longest);
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bmp.width * scale));
    canvas.height = Math.max(1, Math.round(bmp.height * scale));
    const ctx = canvas.getContext('2d');
    if (!ctx) return file;
    ctx.fillStyle = '#fff'; // transparent areas would turn black in a JPEG
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
    bmp.close?.();
    const out = await new Promise<Blob | null>((res) => canvas.toBlob(res, 'image/jpeg', 0.88));
    return out && out.size < file.size ? out : file;
  } catch {
    return file;
  }
}
