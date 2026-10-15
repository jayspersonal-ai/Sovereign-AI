<!-- src/lib/components/DataPanel.svelte -->
<script lang="ts">
  import type { TerminalState } from '../terminal.svelte';
  import { detectImport, downloadJson, fileStamp, plain, slugify } from '../backup';
  import type { AppBackup, RestoreResult } from '../backup';
  import { Download, Upload, Archive } from 'lucide-svelte';

  interface Props {
    terminal: TerminalState;
  }

  let { terminal }: Props = $props();

  // Shared class strings
  const box = 'rounded-lg border border-slate-800 bg-slate-900/30 p-4 space-y-3';
  const heading = 'flex items-center gap-2 text-sm font-mono text-slate-200';
  const btn = 'inline-flex items-center gap-2 px-3 py-2 rounded-lg border text-xs font-mono transition-colors disabled:opacity-50';
  const btnMain = `${btn} bg-indigo-600 border-indigo-500 text-white hover:bg-indigo-500`;
  const btnQuiet = `${btn} bg-slate-900 border-slate-700 text-slate-300 hover:bg-slate-800`;

  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

  // ---------- feedback messages ----------
  type Message = { kind: 'ok' | 'warn' | 'error'; text: string };
  let messages = $state<Message[]>([]);
  const say = (kind: Message['kind'], text: string) => (messages = [...messages, { kind, text }]);
  const msgColor = { ok: 'text-emerald-400', warn: 'text-amber-300', error: 'text-rose-400' };

  let busy = $state(false);

  // ---------- 1. full backup ----------
  const LAST_KEY = 'last_backup_at';
  function readLast(): string | null {
    try { return localStorage.getItem(LAST_KEY); } catch { return null; }
  }
  let lastBackup = $state<string | null>(readLast());
  const lastBackupLabel = $derived.by(() => {
    if (!lastBackup) return 'never';
    const d = new Date(lastBackup);
    return isNaN(d.getTime()) ? 'never' : d.toLocaleString();
  });

  const unsaved = $derived(terminal.unsavedCount());

  function downloadBackup() {
    const backup = terminal.snapshotBackup();
    downloadJson(`backup-${fileStamp()}.json`, backup);
    lastBackup = backup.createdAt;
    try { localStorage.setItem(LAST_KEY, backup.createdAt); } catch { /* ignore */ }
    say('ok', `Backup downloaded: ${plural(backup.characters.length, 'character')}, ${plural(backup.lorebooks.length, 'lorebook')} and the global system prompt. Check your Downloads folder.`);
  }

  // ---------- 2. restore ----------
  let pending = $state<{ filename: string; backup: AppBackup; warnings: string[] } | null>(null);
  let mode = $state<'add' | 'overwrite'>('add');

  const preview = $derived.by(() => {
    if (!pending) return null;
    const have = (list: { id?: string }[], id: string) => list.some((d) => d.id === id);
    const chars = pending.backup.characters;
    const books = pending.backup.lorebooks;
    const charsNew = chars.filter((c) => !have(terminal.characterDocs, c.id)).length;
    const booksNew = books.filter((b) => !have(terminal.lorebookDocs, b.id)).length;
    return { chars: chars.length, charsNew, books: books.length, booksNew, settings: !!pending.backup.settings };
  });

  async function readFile(file: File): Promise<string | null> {
    try {
      return await file.text();
    } catch {
      say('error', `Couldn't read ${file.name}.`);
      return null;
    }
  }

  async function chooseBackup(e: Event & { currentTarget: HTMLInputElement }) {
    const input = e.currentTarget;
    const file = input.files?.[0];
    input.value = ''; // lets you pick the same file again later
    if (!file) return;
    const text = await readFile(file);
    if (text === null) return;
    const found = detectImport(text);
    if (found.kind === 'backup') {
      pending = { filename: file.name, backup: found.backup, warnings: found.warnings };
      mode = 'add';
    } else if (found.kind === 'error') {
      pending = null;
      say('error', `${file.name}: ${found.error}`);
    } else {
      pending = null;
      say('warn', `${file.name} is a single ${found.kind === 'character' ? 'character card' : 'lorebook'}, not a full backup. Use "Import" below instead.`);
    }
  }

  function describeResult(r: RestoreResult): string {
    const part = (label: string, c: RestoreResult['characters']) => {
      const bits = [c.added && `${c.added} added`, c.overwritten && `${c.overwritten} replaced`, c.skipped && `${c.skipped} skipped`, c.failed && `${c.failed} FAILED to save`].filter(Boolean);
      return bits.length ? `${label}: ${bits.join(', ')}` : '';
    };
    const settings = r.settings === 'restored' ? 'global system prompt restored' : r.settings === 'failed' ? 'global system prompt FAILED to save' : '';
    return [part('Characters', r.characters), part('Lorebooks', r.lorebooks), settings].filter(Boolean).join(' · ') || 'Nothing to change.';
  }

  async function runRestore() {
    if (!pending || busy) return;
    busy = true;
    try {
      const result = await terminal.applyBackup(pending.backup, mode);
      const failed = result.characters.failed + result.lorebooks.failed + (result.settings === 'failed' ? 1 : 0);
      say(failed ? 'warn' : 'ok', `Restore finished. ${describeResult(result)}`);
      if (failed) say('warn', 'Some items did not save to the server (connection or conflict). They are kept locally; check each editor\u2019s Save button.');
      pending = null;
    } catch (err) {
      say('error', `Restore failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      busy = false;
    }
  }

  // ---------- 3. share single items ----------
  function exportCharacter(doc: (typeof terminal.characterDocs)[number]) {
    const name = doc.data.data.name || 'character';
    downloadJson(`${slugify(name, 'character')}.card.json`, plain(doc.data));
    say('ok', `Exported "${name}" as a V2 character card.`);
  }

  function exportLorebook(doc: (typeof terminal.lorebookDocs)[number]) {
    const name = doc.data.name || 'lorebook';
    downloadJson(`${slugify(name, 'lorebook')}.lorebook.json`, plain(doc.data));
    say('ok', `Exported lorebook "${name}".`);
  }

  async function importFiles(e: Event & { currentTarget: HTMLInputElement }) {
    const input = e.currentTarget;
    const files = Array.from(input.files ?? []);
    input.value = '';
    if (files.length === 0 || busy) return;
    busy = true;
    try {
      for (const file of files) {
        const text = await readFile(file);
        if (text === null) continue;
        const found = detectImport(text);
        if (found.kind === 'character') {
          const { id, ok } = await terminal.importCharacter(found.card);
          say(ok ? 'ok' : 'warn', `Imported character "${found.card.data.name}" as ${id}.${found.note ? ' ' + found.note : ''}${ok ? '' : ' It did not save to the server yet.'}`);
        } else if (found.kind === 'lorebook') {
          const { id, ok } = await terminal.importLorebook(found.lorebook);
          say(ok ? 'ok' : 'warn', `Imported lorebook "${found.lorebook.name}" as ${id}.${ok ? '' : ' It did not save to the server yet.'}`);
        } else if (found.kind === 'backup') {
          say('warn', `${file.name} is a full backup. Use "Restore from backup" above.`);
        } else {
          say('error', `${file.name}: ${found.error}`);
        }
      }
    } finally {
      busy = false;
    }
  }
</script>

<div class="space-y-4 text-xs">

  <!-- 1. FULL BACKUP -->
  <section class={box}>
    <div class={heading}><Archive class="w-4 h-4 text-indigo-400" /><span>Full backup</span></div>
    <p class="text-slate-400 leading-relaxed">
      One file with all {plural(terminal.characterDocs.length, 'character')}, {plural(terminal.lorebookDocs.length, 'lorebook')} and your global system prompt.
      Chat history lives in the database and isn't included.
    </p>
    {#if unsaved > 0}
      <p class="text-amber-300 leading-relaxed">⚠ {plural(unsaved, 'item')} {unsaved === 1 ? 'has' : 'have'} unsaved changes. The backup includes them, but they aren't saved to the server yet.</p>
    {/if}
    <div class="flex flex-wrap items-center justify-between gap-3">
      <button onclick={downloadBackup} class={btnMain}><Download class="w-4 h-4" /><span>Download backup</span></button>
      <span class="text-[10px] font-mono text-slate-500">Last backup from this device: {lastBackupLabel}</span>
    </div>
  </section>

  <!-- 2. RESTORE -->
  <section class={box}>
    <div class={heading}><Upload class="w-4 h-4 text-emerald-400" /><span>Restore from backup</span></div>
    <label class="{btnQuiet} cursor-pointer w-fit">
      <Upload class="w-4 h-4" /><span>Choose backup file…</span>
      <input type="file" accept=".json,application/json" class="hidden" onchange={chooseBackup} />
    </label>

    {#if pending && preview}
      <div class="rounded-lg border border-slate-700 bg-slate-950 p-3 space-y-3">
        <p class="text-slate-300 font-mono break-all">
          {pending.filename}
          {#if pending.backup.createdAt && !isNaN(new Date(pending.backup.createdAt).getTime())}
            <span class="text-slate-500"> · made {new Date(pending.backup.createdAt).toLocaleString()}</span>
          {/if}
        </p>
        <ul class="text-slate-400 space-y-1 list-disc pl-4">
          <li>{plural(preview.chars, 'character')} ({preview.charsNew} new, {preview.chars - preview.charsNew} already here)</li>
          <li>{plural(preview.books, 'lorebook')} ({preview.booksNew} new, {preview.books - preview.booksNew} already here)</li>
          <li>Global system prompt: {preview.settings ? 'included' : 'not included'}</li>
        </ul>
        {#each pending.warnings as w}
          <p class="text-amber-300">⚠ {w}</p>
        {/each}

        <div class="space-y-2">
          <label class="flex items-start gap-2 cursor-pointer">
            <input type="radio" bind:group={mode} value="add" class="mt-0.5 accent-indigo-500" />
            <span class="text-slate-300 leading-relaxed"><b>Add missing only</b> <span class="text-slate-500">· keeps everything you have now</span></span>
          </label>
          <label class="flex items-start gap-2 cursor-pointer">
            <input type="radio" bind:group={mode} value="overwrite" class="mt-0.5 accent-indigo-500" />
            <span class="text-slate-300 leading-relaxed"><b>Overwrite matching</b> <span class="text-slate-500">· replaces items with the same ID and restores the global system prompt</span></span>
          </label>
          <p class="text-[10px] text-slate-600 leading-relaxed">Nothing is ever deleted. On a fresh install you may see an extra default character or lorebook after restoring; delete the one you don't want.</p>
        </div>

        <div class="flex gap-2">
          <button onclick={runRestore} disabled={busy} class={btnMain}>{busy ? 'Restoring…' : 'Restore'}</button>
          <button onclick={() => (pending = null)} disabled={busy} class={btnQuiet}>Cancel</button>
        </div>
      </div>
    {/if}
  </section>

  <!-- 3. SHARE / MOVE SINGLE ITEMS -->
  <section class={box}>
    <div class={heading}><Download class="w-4 h-4 text-amber-400" /><span>Share or move single items</span></div>

    <div class="space-y-1.5">
      <div class="text-[10px] font-mono text-slate-500 uppercase tracking-wider">Export a character card (V2, works in other frontends)</div>
      {#each terminal.characterDocs as doc (doc.id)}
        <div class="flex items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2">
          <span class="truncate text-slate-200 font-mono">{doc.data.data.name}</span>
          <button onclick={() => exportCharacter(doc)} class={btnQuiet} title="Download this card"><Download class="w-4 h-4" /></button>
        </div>
      {/each}
    </div>

    <div class="space-y-1.5">
      <div class="text-[10px] font-mono text-slate-500 uppercase tracking-wider">Export a lorebook</div>
      {#each terminal.lorebookDocs as doc (doc.id)}
        <div class="flex items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2">
          <span class="truncate text-slate-200 font-mono">{doc.data.name}</span>
          <button onclick={() => exportLorebook(doc)} class={btnQuiet} title="Download this lorebook"><Download class="w-4 h-4" /></button>
        </div>
      {/each}
    </div>

    <div class="space-y-1.5 pt-1">
      <div class="text-[10px] font-mono text-slate-500 uppercase tracking-wider">Import</div>
      <label class="{btnQuiet} cursor-pointer w-fit">
        <Upload class="w-4 h-4" /><span>Import card or lorebook files…</span>
        <input type="file" multiple accept=".json,application/json" class="hidden" onchange={importFiles} />
      </label>
      <p class="text-[10px] text-slate-600 leading-relaxed">
        Accepts character cards (V1, V2, V3) and lorebooks exported from here. Imports are added as new items and never overwrite existing ones.
        SillyTavern world-info files aren't supported yet.
      </p>
    </div>
  </section>

  <!-- MESSAGES -->
  {#if messages.length > 0}
    <section class="rounded-lg border border-slate-800 bg-slate-950 p-3 space-y-1.5">
      {#each messages as m}
        <p class="{msgColor[m.kind]} leading-relaxed">{m.text}</p>
      {/each}
      <button onclick={() => (messages = [])} class="text-[10px] font-mono text-slate-500 hover:text-slate-300">Clear</button>
    </section>
  {/if}
</div>
