<!-- src/lib/components/SaveBar.svelte -->
<script lang="ts">
  import { Save, Check } from 'lucide-svelte';
  import type { SyncedDoc } from '../persist.svelte';

  interface Props {
    doc: SyncedDoc<any> | undefined;
  }

  let { doc }: Props = $props();
</script>

{#if doc}
  <div class="flex flex-wrap items-center gap-2 font-mono text-[11px]">
    {#if doc.status === 'conflict'}
      <span class="text-amber-300">⚠ Changed on another device.</span>
      <button onclick={() => doc?.keepMine()} class="px-2 py-1 rounded border border-amber-500/40 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20">Keep mine</button>
      <button onclick={() => doc?.useTheirs()} class="px-2 py-1 rounded border border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800">Use theirs</button>
    {:else}
      {#if doc.error}
        <span class="text-rose-400">{doc.error}</span>
      {/if}
      {#if doc.restoredDraft && doc.status === 'dirty'}
        <span class="text-slate-500">Restored unsaved draft</span>
      {/if}
      {#if doc.status === 'dirty' || doc.status === 'error'}
        <button onclick={() => doc?.discardChanges()} class="px-2 py-1 rounded text-slate-500 hover:text-slate-300">Revert</button>
      {/if}
      <button
        onclick={() => doc?.save()}
        disabled={!doc.needsSave || doc.status === 'saving'}
        class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border transition-colors disabled:opacity-60
          {doc.status === 'error' ? 'bg-rose-600/20 border-rose-500/40 text-rose-300 hover:bg-rose-600/30'
            : doc.needsSave ? 'bg-indigo-600 border-indigo-500 text-white hover:bg-indigo-500'
            : 'bg-slate-900 border-slate-800 text-emerald-400'}"
      >
        {#if doc.needsSave || doc.status === 'saving'}
          <Save class="w-3 h-3" />
        {:else}
          <Check class="w-3 h-3" />
        {/if}
        <span>{doc.statusLabel}</span>
      </button>
    {/if}
  </div>
{/if}
