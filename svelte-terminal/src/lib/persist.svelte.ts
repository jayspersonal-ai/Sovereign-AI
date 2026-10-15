// src/lib/persist.svelte.ts
//
// SyncedDoc<T> wraps one JSON document on the data server (a character card,
// a lorebook, or the settings file) and handles everything between "you typed
// something" and "it's safely on the server":
//
//   - Every edit is autosaved as a DRAFT in localStorage (no network).
//   - The server only changes when you call save() (your Save button).
//   - The last server copy is cached, so the app still opens with no signal.
//   - Saves carry the version you started from; if your other device saved first,
//     you get a 'conflict' and choose which copy to keep.
//
// Usage:
//   const settings = new SyncedDoc({ collection: 'settings', defaults: { globalSystemPrompt: '' } });
//   await settings.load();
//   <textarea bind:value={settings.data.globalSystemPrompt}></textarea>
//   <button onclick={() => settings.save()} disabled={!settings.needsSave}>{settings.statusLabel}</button>

import { untrack } from 'svelte';

export type SaveStatus =
  | 'loading'
  | 'clean' // matches the server
  | 'dirty' // local edits not yet saved to the server
  | 'saving'
  | 'saved' // just saved successfully
  | 'offline' // server unreachable on load; showing the cached copy
  | 'error' // last save failed (network or server error); draft is kept
  | 'conflict'; // the server copy changed since you started editing

export interface Stored<T> {
  id?: string;
  version: string;
  data: T;
}

const API = '/api/data';
const LOAD_TIMEOUT_MS = 8000;
const SAVE_TIMEOUT_MS = 15000;
const DRAFT_DEBOUNCE_MS = 300;

function docUrl(collection: string, id?: string): string {
  return id ? `${API}/${collection}/${encodeURIComponent(id)}` : `${API}/${collection}`;
}

// A hung request on a weak signal should fail, not spin forever.
async function fetchWithTimeout(url: string, init: RequestInit, ms: number): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function readLocal<V>(key: string): V | null {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as V) : null;
  } catch {
    return null;
  }
}

function writeLocal(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage full or blocked: the in-memory copy still works */
  }
}

function removeLocal(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

// Anything in browser storage may be left over from an older version of this file, or corrupt.
// Check the shape before trusting it; a bad entry is discarded instead of crashing the load.
function isDraft(v: unknown): v is { snap: string; base: string | null; ts: number } {
  return !!v && typeof v === 'object' && typeof (v as { snap?: unknown }).snap === 'string' && 'base' in v;
}
function isCachedCopy(v: unknown): v is { data: unknown; version: string } {
  return !!v && typeof v === 'object' && 'data' in v && typeof (v as { version?: unknown }).version === 'string';
}

// ---------- collection helpers (listing / deleting / new ids) ----------

export const dataApi = {
  async list<T>(collection: 'characters' | 'lorebooks'): Promise<Stored<T>[]> {
    const res = await fetchWithTimeout(docUrl(collection), { cache: 'no-store' }, LOAD_TIMEOUT_MS);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async remove(collection: 'characters' | 'lorebooks', id: string): Promise<void> {
    const res = await fetchWithTimeout(docUrl(collection, id), { method: 'DELETE' }, SAVE_TIMEOUT_MS);
    if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
    removeLocal(`draft:${collection}:${id}`);
    removeLocal(`cache:${collection}:${id}`);
  },

  newId(prefix = 'doc'): string {
    const rand =
      typeof crypto !== 'undefined' && crypto.randomUUID
        ? crypto.randomUUID().slice(0, 8)
        : Math.random().toString(36).slice(2, 10);
    return `${prefix}-${rand}`;
  }
};

// ---------- SyncedDoc ----------

export interface SyncedDocOptions<T> {
  collection: 'settings' | 'state' | 'characters' | 'lorebooks';
  /** Required for characters / lorebooks, omitted for settings. */
  id?: string;
  /** Used when the doc doesn't exist on the server yet. */
  defaults: T;
}

interface Draft {
  snap: string; // JSON of the edited doc
  base: string | null; // server version the edits started from
  ts: number;
}

interface CachedCopy<T> {
  data: T;
  version: string;
}

export class SyncedDoc<T extends object> {
  data = $state<T>(undefined as unknown as T);
  status = $state<SaveStatus>('loading');
  error = $state<string | null>(null);
  /** True when the doc on screen came from a leftover local draft. */
  restoredDraft = $state(false);
  /** Set when status === 'conflict': the copy currently on the server (null if it was deleted). */
  conflict = $state<CachedCopy<T> | null>(null);

  readonly collection: SyncedDocOptions<T>['collection'];
  readonly id: string | undefined;

  #defaults: T;
  #version: string | null = null;
  #savedSnap = '';
  #draftKey: string;
  #cacheKey: string;
  #draftTimer: ReturnType<typeof setTimeout> | undefined;
  #stopEffects: (() => void) | undefined;
  #detach: (() => void) | undefined;

  constructor(opts: SyncedDocOptions<T>) {
    this.collection = opts.collection;
    this.id = opts.id;
    this.#defaults = opts.defaults;
    const key = opts.id ? `${opts.collection}:${opts.id}` : opts.collection;
    this.#draftKey = `draft:${key}`;
    this.#cacheKey = `cache:${key}`;

    this.data = structuredClone(opts.defaults);
    this.#savedSnap = JSON.stringify(this.data);

    // Watch the doc (deeply) so any edit marks it dirty and refreshes the local draft.
    this.#stopEffects = $effect.root(() => {
      $effect(() => {
        const snap = JSON.stringify(this.data);
        untrack(() => this.#onEdit(snap));
      });
    });

    this.#detach = this.#attachBrowserListeners();
  }

  // ----- derived UI helpers -----

  get needsSave(): boolean {
    return (
      this.status === 'dirty' ||
      this.status === 'error' ||
      (this.#version === null && this.status !== 'loading' && this.status !== 'saving')
    );
  }

  get statusLabel(): string {
    switch (this.status) {
      case 'loading':
        return 'Loading…';
      case 'clean':
        return this.#version === null ? 'Save changes' : 'Saved';
      case 'saved':
        return 'Saved';
      case 'dirty':
        return 'Save changes';
      case 'saving':
        return 'Saving…';
      case 'offline':
        return 'Offline copy';
      case 'error':
        return 'Save failed. Tap to retry';
      case 'conflict':
        return 'Changed on another device';
    }
  }

  // ----- loading -----

  async load(): Promise<void> {
    try {
      await this.#load();
    } catch (err) {
      // Never let one document's load take the whole app down.
      console.error(`[persist] failed to load ${this.collection}${this.id ? '/' + this.id : ''}`, err);
      this.status = 'error';
      this.error = err instanceof Error ? err.message : 'Failed to load';
    }
  }

  async #load(): Promise<void> {
    this.status = 'loading';
    this.error = null;
    this.conflict = null;

    const result = await this.#fetchServer();

    // Only trust a draft that has the expected shape and holds valid JSON.
    let draft: Draft | null = null;
    const draftRaw = readLocal<unknown>(this.#draftKey);
    if (isDraft(draftRaw)) {
      try {
        JSON.parse(draftRaw.snap);
        draft = draftRaw;
      } catch {
        /* corrupt draft: dropped below */
      }
    }
    if (draftRaw && !draft) removeLocal(this.#draftKey);

    // Pick the base copy: server if reachable, otherwise the cache, otherwise defaults.
    let base: CachedCopy<T> | null;
    if (result.kind === 'ok') {
      base = result.doc;
      writeLocal(this.#cacheKey, base);
    } else if (result.kind === 'missing') {
      base = null;
      removeLocal(this.#cacheKey);
    } else {
      const cachedRaw = readLocal<unknown>(this.#cacheKey);
      base = isCachedCopy(cachedRaw) ? (cachedRaw as CachedCopy<T>) : null;
    }

    const baseData = base ? base.data : structuredClone(this.#defaults);
    const baseSnap = JSON.stringify(baseData);

    // A leftover draft that differs from the base: restore it.
    if (draft && draft.snap !== baseSnap) {
      this.#savedSnap = baseSnap;
      this.data = JSON.parse(draft.snap) as T;
      this.#version = draft.base;
      this.restoredDraft = true;

      if (result.kind === 'ok' && result.doc.version !== draft.base) {
        // The server moved on while this draft sat in storage.
        this.conflict = result.doc;
        this.status = 'conflict';
      } else if (result.kind === 'missing' && draft.base !== null) {
        this.conflict = null;
        this.status = 'conflict';
      } else {
        this.status = 'dirty';
      }
      return;
    }

    // No (differing) draft: show the base copy.
    removeLocal(this.#draftKey);
    this.restoredDraft = false;
    this.#version = base ? base.version : null;
    this.#savedSnap = baseSnap;
    this.data = baseData;
    this.status = result.kind === 'unreachable' ? 'offline' : 'clean';
  }

  // ----- saving -----

  async save(): Promise<void> {
    if (this.status === 'saving' || this.status === 'loading' || this.status === 'conflict') return;

    const snap = JSON.stringify(this.data);
    if (snap === this.#savedSnap && this.#version !== null) return;

    this.status = 'saving';
    this.error = null;
    clearTimeout(this.#draftTimer);
    this.#writeDraft(snap); // belt and braces: the draft is current before we go out

    try {
      const res = await fetchWithTimeout(
        docUrl(this.collection, this.id),
        {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            ...(this.#version ? { 'If-Match': this.#version } : {})
          },
          body: snap
        },
        SAVE_TIMEOUT_MS
      );

      if (res.status === 409) {
        const body = await res.json();
        this.conflict = body.current ?? null;
        this.status = 'conflict';
        return;
      }
      if (!res.ok) throw new Error(`Server returned HTTP ${res.status}`);

      const { version } = (await res.json()) as { version: string };
      this.#version = version;
      this.#savedSnap = snap;
      writeLocal(this.#cacheKey, { data: JSON.parse(snap), version } satisfies CachedCopy<T>);

      // Did the user keep typing while the request was in flight?
      if (JSON.stringify(this.data) === snap) {
        removeLocal(this.#draftKey);
        this.restoredDraft = false;
        this.status = 'saved';
      } else {
        this.status = 'dirty';
        this.#scheduleDraft();
      }
    } catch (err) {
      this.status = 'error';
      this.error =
        err instanceof DOMException && err.name === 'AbortError'
          ? 'Timed out. The connection may be weak.'
          : err instanceof Error
            ? err.message
            : 'Unknown error';
    }
  }

  // ----- conflict resolution -----

  /** Overwrite the server copy with what's on screen. */
  async keepMine(): Promise<void> {
    if (this.status !== 'conflict') return;
    this.#version = this.conflict ? this.conflict.version : null;
    this.conflict = null;
    this.status = 'dirty';
    await this.save();
  }

  /** Throw away local edits and take the server copy. */
  useTheirs(): void {
    if (this.status !== 'conflict') return;
    const theirs = this.conflict;
    this.conflict = null;
    removeLocal(this.#draftKey);
    this.restoredDraft = false;

    // `theirs` is reactive state (a Svelte proxy), which structuredClone can't copy; round-trip through JSON instead.
    const data = theirs ? (JSON.parse(JSON.stringify(theirs.data)) as T) : structuredClone(this.#defaults);
    this.#version = theirs ? theirs.version : null;
    this.#savedSnap = JSON.stringify(data);
    this.data = data;
    if (theirs) writeLocal(this.#cacheKey, theirs);
    this.status = 'clean';
  }

  /** Drop unsaved edits and reload from the server. */
  async discardChanges(): Promise<void> {
    clearTimeout(this.#draftTimer);
    removeLocal(this.#draftKey);
    this.restoredDraft = false;
    await this.load();
  }

  // ----- lifecycle -----

  dispose(): void {
    clearTimeout(this.#draftTimer);
    this.#stopEffects?.();
    this.#detach?.();
  }

  // ----- internals -----

  async #fetchServer(): Promise<
    { kind: 'ok'; doc: CachedCopy<T> } | { kind: 'missing' } | { kind: 'unreachable' }
  > {
    try {
      const res = await fetchWithTimeout(docUrl(this.collection, this.id), { cache: 'no-store' }, LOAD_TIMEOUT_MS);
      if (res.status === 404) return { kind: 'missing' };
      if (!res.ok) return { kind: 'unreachable' };
      const body = (await res.json()) as Stored<T>;
      return { kind: 'ok', doc: { data: body.data, version: body.version } };
    } catch {
      return { kind: 'unreachable' };
    }
  }

  #onEdit(snap: string): void {
    if (this.status === 'loading' || this.status === 'saving' || this.status === 'conflict') {
      if (snap !== this.#savedSnap) this.#scheduleDraft();
      return;
    }
    if (snap === this.#savedSnap) {
      // Edited back to exactly what's on the server.
      if (this.status === 'dirty') {
        clearTimeout(this.#draftTimer);
        removeLocal(this.#draftKey);
        this.status = 'clean';
      }
      return;
    }
    this.status = 'dirty';
    this.#scheduleDraft();
  }

  #scheduleDraft(): void {
    clearTimeout(this.#draftTimer);
    this.#draftTimer = setTimeout(() => this.#writeDraft(JSON.stringify(this.data)), DRAFT_DEBOUNCE_MS);
  }

  #writeDraft(snap: string): void {
    if (snap === this.#savedSnap) {
      removeLocal(this.#draftKey);
      return;
    }
    writeLocal(this.#draftKey, { snap, base: this.#version, ts: Date.now() } satisfies Draft);
  }

  /** Write any pending draft right now (called when the tab is hidden or closing). */
  #flushDraft(): void {
    if (this.#draftTimer === undefined) return;
    clearTimeout(this.#draftTimer);
    this.#draftTimer = undefined;
    this.#writeDraft(JSON.stringify(this.data));
  }

  /** If nothing is pending locally, quietly pick up changes saved from the other device. */
  async #refreshIfClean(): Promise<void> {
    if (this.status !== 'clean' && this.status !== 'saved' && this.status !== 'offline') return;
    const result = await this.#fetchServer();
    // Re-check: the user may have started typing during the request.
    if (this.status !== 'clean' && this.status !== 'saved' && this.status !== 'offline') return;
    if (result.kind === 'unreachable') return;

    if (result.kind === 'ok') {
      if (result.doc.version !== this.#version) {
        const data = structuredClone(result.doc.data);
        this.#version = result.doc.version;
        this.#savedSnap = JSON.stringify(data);
        this.data = data;
        writeLocal(this.#cacheKey, result.doc);
      }
      this.status = 'clean';
    }
  }

  #attachBrowserListeners(): (() => void) | undefined {
    if (typeof window === 'undefined' || typeof document === 'undefined') return undefined;

    const onVisibility = () => {
      if (document.visibilityState === 'hidden') this.#flushDraft();
      else void this.#refreshIfClean();
    };
    const onPageHide = () => this.#flushDraft();

    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('pagehide', onPageHide);
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('pagehide', onPageHide);
    };
  }
}
