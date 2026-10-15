// src/lib/backup.ts
//
// Pure helpers for backup, restore, and importing/exporting single items.
// Nothing here touches the server or Svelte state, so it is easy to test.
//
// File formats:
//   Backup     {"format":"backup","version":1,"settings":{...},"characters":[{id,data}],"lorebooks":[{id,data}]}
//   Card       a Character Card V2 object (V1 and V3 cards are converted on import)
//   Lorebook   {"name": "...", "nodes": [...folders and pages...]}  (identical to the file in data/lorebooks)

import type { CharacterCardV2, CharacterCardData, Lorebook, LoreNode, SettingsData } from './types';

export const BACKUP_FORMAT = 'backup';
const LEGACY_BACKUP_FORMATS = ['lyra-backup']; // older backup files are still accepted
export const ID_RE = /^[A-Za-z0-9_-]{1,80}$/;

export interface AppBackup {
  format: typeof BACKUP_FORMAT;
  version: 1;
  createdAt: string;
  settings?: SettingsData;
  characters: { id: string; data: CharacterCardV2 }[];
  lorebooks: { id: string; data: Lorebook }[];
}

export type DetectResult =
  | { kind: 'backup'; backup: AppBackup; warnings: string[] }
  | { kind: 'character'; card: CharacterCardV2; note?: string }
  | { kind: 'lorebook'; lorebook: Lorebook; note?: string }
  | { kind: 'error'; error: string };

export interface Counts {
  added: number;
  overwritten: number;
  skipped: number;
  failed: number;
}
export interface RestoreResult {
  characters: Counts;
  lorebooks: Counts;
  settings: 'restored' | 'untouched' | 'failed';
}

// ---------- small utilities ----------

const isObject = (v: unknown): v is Record<string, any> => !!v && typeof v === 'object' && !Array.isArray(v);
const str = (v: unknown, fallback = ''): string => (typeof v === 'string' ? v : fallback);

/** Deep copy into plain JSON (Svelte state proxies can't be structuredClone'd). */
export function plain<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

/** "Mara the Pilot!" -> "mara-the-pilot". Always returns something that passes ID_RE. */
export function slugify(name: string, fallback = 'item'): string {
  const slug = (name || '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60)
    .replace(/-+$/g, '');
  return slug || fallback;
}

/** base, base-2, base-3 ... first one not in `taken`. */
export function uniqueId(base: string, taken: Set<string>): string {
  if (!taken.has(base)) return base;
  let n = 2;
  while (taken.has(`${base}-${n}`)) n++;
  return `${base}-${n}`;
}

export function fileStamp(d: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** Start a browser download of `data` as a pretty-printed .json file. */
export function downloadJson(filename: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2) + '\n'], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

// ---------- character cards ----------

const CARD_DEFAULTS: CharacterCardData = {
  name: '',
  description: '',
  personality: '',
  scenario: '',
  first_mes: '',
  mes_example: '',
  creator_notes: '',
  system_prompt: '',
  post_history_instructions: '',
  alternate_greetings: [],
  tags: [],
  creator: '',
  character_version: '1.0',
  extensions: {}
};

function completeCardData(raw: Record<string, any>): CharacterCardData {
  const data = { ...CARD_DEFAULTS, ...raw } as CharacterCardData;
  for (const key of ['name', 'description', 'personality', 'scenario', 'first_mes', 'mes_example', 'creator_notes', 'system_prompt', 'post_history_instructions', 'creator', 'character_version'] as const) {
    if (typeof data[key] !== 'string') data[key] = CARD_DEFAULTS[key] as never;
  }
  data.alternate_greetings = Array.isArray(data.alternate_greetings) ? data.alternate_greetings.filter((g) => typeof g === 'string') : [];
  data.tags = Array.isArray(data.tags) ? data.tags.filter((t) => typeof t === 'string') : [];
  if (!isObject(data.extensions)) data.extensions = {};
  if (!data.name.trim()) data.name = 'Imported Character';
  return data;
}

/**
 * Accepts V2 cards, V3 cards and flat V1 cards and returns a V2 card, or null if it isn't a card.
 * Fields this app doesn't know (character_book, extensions, V3-only fields) are kept, not dropped.
 */
export function normalizeCard(raw: unknown): { card: CharacterCardV2; note?: string } | null {
  if (!isObject(raw)) return null;

  if ((raw.spec === 'chara_card_v2' || raw.spec === 'chara_card_v3') && isObject(raw.data)) {
    const converted = raw.spec === 'chara_card_v3';
    return {
      card: { ...raw, spec: 'chara_card_v2', spec_version: '2.0', data: completeCardData(raw.data) } as CharacterCardV2,
      note: converted ? 'V3 card imported as V2; V3-only fields are kept in the file but not used.' : undefined
    };
  }

  // V1: flat card with the fields at the top level
  if (typeof raw.name === 'string' && !('data' in raw) && ('description' in raw || 'first_mes' in raw || 'personality' in raw)) {
    const data = completeCardData({
      name: raw.name,
      description: str(raw.description),
      personality: str(raw.personality),
      scenario: str(raw.scenario),
      first_mes: str(raw.first_mes),
      mes_example: str(raw.mes_example)
    });
    return { card: { spec: 'chara_card_v2', spec_version: '2.0', data }, note: 'V1 card converted to V2.' };
  }
  return null;
}

// ---------- lorebooks ----------

function sanitizeNodes(raw: unknown, counter = { n: 0 }): LoreNode[] {
  if (!Array.isArray(raw)) return [];
  const out: LoreNode[] = [];
  for (const n of raw) {
    if (!isObject(n)) continue;
    const id = typeof n.id === 'string' && n.id ? n.id : `imp-${Date.now()}-${counter.n++}`;
    const title = str(n.title, 'Untitled');
    if (n.type === 'folder') {
      out.push({ id, type: 'folder', title, children: sanitizeNodes(n.children, counter) });
    } else if (n.type === 'page') {
      out.push({ id, type: 'page', title, content: str(n.content), isActive: typeof n.isActive === 'boolean' ? n.isActive : true });
    }
  }
  return out;
}

export function normalizeLorebook(raw: unknown): Lorebook | null {
  if (!isObject(raw) || !Array.isArray(raw.nodes)) return null;
  return { name: str(raw.name, 'Imported Lorebook').trim() || 'Imported Lorebook', nodes: sanitizeNodes(raw.nodes) };
}

// ---------- backups ----------

function parseBackup(raw: Record<string, any>): DetectResult {
  if (raw.version !== 1) {
    return { kind: 'error', error: `This backup is version ${String(raw.version)}; this app reads version 1. Update the app and try again.` };
  }
  const warnings: string[] = [];
  const takenChars = new Set<string>();
  const takenBooks = new Set<string>();

  const characters: AppBackup['characters'] = [];
  for (const item of Array.isArray(raw.characters) ? raw.characters : []) {
    const norm = isObject(item) ? normalizeCard(item.data) : null;
    if (!isObject(item) || !norm || typeof item.id !== 'string' || !ID_RE.test(item.id) || takenChars.has(item.id)) {
      warnings.push('Skipped a character entry that was invalid or had a duplicate or unusable ID.');
      continue;
    }
    takenChars.add(item.id);
    characters.push({ id: item.id, data: norm.card });
  }

  const lorebooks: AppBackup['lorebooks'] = [];
  for (const item of Array.isArray(raw.lorebooks) ? raw.lorebooks : []) {
    const norm = isObject(item) ? normalizeLorebook(item.data) : null;
    if (!isObject(item) || !norm || typeof item.id !== 'string' || !ID_RE.test(item.id) || takenBooks.has(item.id)) {
      warnings.push('Skipped a lorebook entry that was invalid or had a duplicate or unusable ID.');
      continue;
    }
    takenBooks.add(item.id);
    lorebooks.push({ id: item.id, data: norm });
  }

  let settings: SettingsData | undefined;
  if (isObject(raw.settings) && typeof raw.settings.globalSystemPrompt === 'string') {
    settings = { ...raw.settings, globalSystemPrompt: raw.settings.globalSystemPrompt } as SettingsData;
  } else if (raw.settings !== undefined) {
    warnings.push('The global system prompt in this backup was unreadable and will not be restored.');
  }

  if (characters.length === 0 && lorebooks.length === 0 && !settings) {
    return { kind: 'error', error: 'This backup has nothing usable in it.' };
  }
  return {
    kind: 'backup',
    backup: { format: BACKUP_FORMAT, version: 1, createdAt: str(raw.createdAt), settings, characters, lorebooks },
    warnings
  };
}

// ---------- the one entry point the UI uses ----------

/** Work out what a chosen file is: a backup, a character card, or a lorebook. */
export function detectImport(text: string): DetectResult {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return { kind: 'error', error: 'Not a valid JSON file.' };
  }
  if (!isObject(raw)) return { kind: 'error', error: 'Not a card, lorebook or backup file.' };

  if (raw.format === BACKUP_FORMAT || (typeof raw.format === 'string' && LEGACY_BACKUP_FORMATS.includes(raw.format))) return parseBackup(raw);

  const card = normalizeCard(raw);
  if (card) return { kind: 'character', card: card.card, note: card.note };

  const lorebook = normalizeLorebook(raw);
  if (lorebook) return { kind: 'lorebook', lorebook };

  if (isObject(raw.entries)) {
    return { kind: 'error', error: 'This looks like a SillyTavern world-info file. That format isn\u2019t supported yet.' };
  }
  return { kind: 'error', error: 'Not a recognised character card, lorebook or backup.' };
}
