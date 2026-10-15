// src/lib/media.ts
//
// Helpers for the workspace panel. Items arrive from the gateway as JSON and can be incomplete or
// shaped differently than expected, so they are cleaned up here before the panel sees them.

import type { WorkspaceItem, WorkspaceItemType } from './types';

const KNOWN_TYPES: WorkspaceItemType[] = ['image', 'text', 'json'];
const IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp|svg|avif)(\?|#|$)/i;

const str = (v: unknown): string | undefined => (typeof v === 'string' && v.trim() ? v : undefined);

function looksLikeImage(v?: string): boolean {
  return !!v && (/^data:image\//i.test(v) || IMAGE_EXT.test(v));
}

function baseName(path?: string): string | undefined {
  if (!path) return undefined;
  const clean = path.split(/[?#]/)[0].replace(/[\\/]+$/, '');
  const last = clean.split(/[\\/]/).pop();
  return last || undefined;
}

/**
 * Turn whatever the gateway sent into a complete WorkspaceItem.
 * Missing id/type/title/timestamp are filled in; unknown extra fields are kept so the Raw tab still shows them.
 */
export function normalizeWorkspaceItem(raw: unknown): WorkspaceItem {
  const r: Record<string, any> = raw && typeof raw === 'object' && !Array.isArray(raw) ? (raw as Record<string, any>) : { content: raw == null ? '' : String(raw) };

  const url = str(r.url) ?? str(r.src) ?? str(r.image_url);
  const filepath = str(r.filepath) ?? str(r.path);
  let content: string | undefined = typeof r.content === 'string' ? r.content : undefined;
  if (content === undefined && r.content != null) {
    try {
      content = JSON.stringify(r.content, null, 2);
    } catch {
      content = String(r.content);
    }
  }

  const type: WorkspaceItemType = KNOWN_TYPES.includes(r.type)
    ? r.type
    : looksLikeImage(url) || looksLikeImage(filepath) || looksLikeImage(content)
      ? 'image'
      : 'text';

  const when = new Date(r.timestamp ?? Date.now());
  return {
    ...r,
    id: str(r.id) ?? `ws-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    type,
    title: str(r.title) ?? baseName(filepath) ?? baseName(url) ?? 'Untitled',
    filepath,
    url,
    content,
    source: r.source === 'viewed' || r.source === 'created' ? r.source : undefined,
    timestamp: isNaN(when.getTime()) ? new Date() : when
  };
}

function sniffImageMime(b64: string): string {
  if (b64.startsWith('/9j/')) return 'image/jpeg';
  if (b64.startsWith('iVBOR')) return 'image/png';
  if (b64.startsWith('R0lGOD')) return 'image/gif';
  if (b64.startsWith('UklGR')) return 'image/webp';
  if (b64.startsWith('PHN2Z') || b64.startsWith('PD94b')) return 'image/svg+xml';
  return 'image/png';
}

/**
 * The string to put in <img src>, or null if the item has nothing displayable.
 * Accepts http(s) URLs, same-site paths, blob:/data: URLs, and raw base64 image data.
 */
export function resolveImageSrc(item: { url?: string; content?: string }): string | null {
  for (const candidate of [item.url, item.content]) {
    const v = (candidate ?? '').trim();
    if (!v) continue;
    if (/^file:/i.test(v)) continue; // browsers never load these
    if (/^data:image\//i.test(v) || /^blob:/i.test(v)) return v;
    if (/^(https?:)?\/\/\S+$/i.test(v)) return v;
    // Raw image data. JPEG base64 starts with "/9j/", so this must be checked before treating a leading "/" as a path.
    const compact = v.replace(/\s+/g, '');
    if (compact.length > 100 && /^[A-Za-z0-9+/]+={0,2}$/.test(compact) && (!compact.startsWith('/') || compact.startsWith('/9j/'))) {
      return `data:${sniffImageMime(compact)};base64,${compact}`;
    }
    if (v.startsWith('/') && v.length < 2048 && !/\s/.test(v)) return v;
  }
  return null;
}

/** A plain-language reason an image address probably failed, for the error card. */
export function describeImageProblem(src: string, pageHostname = ''): string {
  if (/^data:/i.test(src)) return 'The image data could not be decoded. It may be cut off or not actually an image.';
  if (/^blob:/i.test(src)) return 'This temporary image link is no longer valid. Ask for the image again.';

  let url: URL | null = null;
  try {
    url = new URL(src, 'http://app.invalid');
  } catch {
    /* handled below */
  }
  if (!url) return 'The image address is not valid.';

  if (url.hostname === 'app.invalid') {
    return 'This path is not served by the app on this port, so the browser got a web page instead of an image. The gateway needs to send the image as a data URL, or through an address the app proxies.';
  }
  const host = url.hostname;
  const isIp = /^\d{1,3}(\.\d{1,3}){3}$/.test(host) || host.includes(':');
  if (host === 'localhost' || host === '127.0.0.1' || host === '::1' || host === '[::1]') {
    if (pageHostname && pageHostname !== 'localhost' && pageHostname !== '127.0.0.1') {
      return `"${host}" means this device, not your PC, so the image can't be found from here. The gateway needs to send the image as a data URL, or through an address this device can reach.`;
    }
  } else if (!host.includes('.') && !isIp) {
    return `"${host}" looks like a Docker service name. Only containers can reach it, not a browser. The gateway needs to send the image as a data URL, or serve it through the app's own address.`;
  }
  return 'The address did not return an image. It may need a login, may have expired, may be blocked by the other server, or the service may be down.';
}

const LONG_STRING = 300;

/** The item as pretty JSON. With `truncate`, long strings (like base64 images) are shortened so the view stays readable. */
export function itemToJson(item: unknown, truncate = true): string {
  return JSON.stringify(
    item,
    (_key, value) =>
      truncate && typeof value === 'string' && value.length > LONG_STRING
        ? `${value.slice(0, 120)}\u2026 (${value.length.toLocaleString('en-US')} characters)`
        : value,
    2
  );
}
