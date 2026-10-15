<script lang="ts">
  import { onMount } from 'svelte';
  import { BrainCircuit, RefreshCw, Edit2, Trash2, Save, X, Tag, Activity } from 'lucide-svelte';
  import { memory, errorText } from '../api';

  const PAGE_SIZE = 50;

  let memories = $state<any[]>([]);
  let total = $state<number | null>(null);
  let approvedTones = $state<string[]>([]);
  let isLoading = $state(false);
  let hasMore = $state(false);
  let errorMsg = $state('');

  let editingId = $state<string | null>(null);
  let editForm = $state<any>({});
  let isSaving = $state(false);

  // Which memory has its full stored text open
  let expandedId = $state<string | null>(null);

  function fail(action: string, err: unknown) {
    errorMsg = `${action}: ${errorText(err)}`;
  }

  function formatDate(unixSeconds: number | null | undefined) {
    return unixSeconds ? new Date(unixSeconds * 1000).toLocaleDateString() : 'undated';
  }

  function isPending(value: string | null | undefined) {
    return String(value ?? '').startsWith('pending:');
  }

  // True when this memory is one piece of a longer, split conversation
  function inChain(mem: any) {
    return Boolean(mem.continued_from_parent_id) || (mem.chain_root_id && mem.chain_root_id !== mem.id);
  }

  // reset = true starts over from the newest memory; false appends the next page
  async function loadVault(reset: boolean) {
    const offset = reset ? 0 : memories.length;
    isLoading = true;
    try {
      const res = await memory(`/memories/vault?limit=${PAGE_SIZE}&offset=${offset}`);
      const page: any[] = res.data ?? [];
      if (reset) {
        memories = page;
        editingId = null;
        expandedId = null;
      } else {
        const known = new Set(memories.map((m) => m.id));
        memories = [...memories, ...page.filter((m) => !known.has(m.id))];
      }
      hasMore = page.length === PAGE_SIZE;
      errorMsg = '';
    } catch (err) {
      fail('Could not load the vault', err);
    }
    isLoading = false;
    if (reset) loadTotal();
  }

  // The count is a nicety; the list still works without it
  async function loadTotal() {
    try {
      const res = await memory('/memories/stats');
      total = res.stats?.total_memories ?? null;
    } catch {
      total = null;
    }
  }

  // The approved tone list lives in the memory engine's schema. If it can't be
  // fetched, the tone field falls back to free text and the server still checks it.
  async function loadTaxonomy() {
    try {
      const res = await memory('/memories/taxonomy');
      approvedTones = res.tones ?? [];
    } catch {
      approvedTones = [];
    }
  }

  async function deleteMemory(mem: any) {
    const warning = inChain(mem)
      ? 'This memory is one part of a longer chain. Only this part will be deleted. Permanently purge it from LanceDB?'
      : 'Permanently purge this memory from LanceDB?';
    if (!confirm(warning)) return;
    try {
      await memory(`/memories/${encodeURIComponent(mem.id)}`, 'DELETE');
      memories = memories.filter((m) => m.id !== mem.id);
      if (total !== null) total = Math.max(0, total - 1);
    } catch (err) {
      fail('Could not delete memory', err);
    }
  }

  function startEdit(mem: any) {
    editingId = mem.id;
    editForm = {
      emotional_tone: mem.emotional_tone || 'neutral_logistical',
      qos_priority_weight: mem.qos_priority_weight || 3,
      topic_tags: (mem.topic_tags ?? []).join(', ')
    };
  }

  function clampPriority(value: unknown, fallback: number) {
    const n = parseInt(String(value), 10);
    if (Number.isNaN(n)) return fallback;
    return Math.min(5, Math.max(1, n));
  }

  async function saveMetadata(mem: any) {
    const tags = String(editForm.topic_tags ?? '')
      .split(',')
      .map((t: string) => t.trim())
      .filter(Boolean);
    if (tags.length === 0) {
      errorMsg = "Could not save memory: add at least one topic tag (use 'uncategorized-general' if nothing fits).";
      return;
    }
    const payload = {
      emotional_tone: String(editForm.emotional_tone ?? '').trim() || 'neutral_logistical',
      qos_priority_weight: clampPriority(editForm.qos_priority_weight, mem.qos_priority_weight || 3),
      topic_tags: tags
    };

    isSaving = true;
    try {
      const result = await memory(`/memories/${encodeURIComponent(mem.id)}/metadata`, 'PATCH', payload);
      // Show what the server actually stored (it may have marked a new tag as pending)
      const saved = result?.data ?? payload;
      memories = memories.map((m) => (m.id === mem.id ? { ...m, ...saved } : m));
      editingId = null;
      errorMsg = '';
    } catch (err) {
      fail('Could not save memory', err);
    }
    isSaving = false;
  }

  onMount(() => {
    loadVault(true);
    loadTaxonomy();
  });
</script>

<div class="flex flex-col h-full bg-slate-950">
  <div class="flex flex-wrap items-center justify-between gap-3 p-3 md:p-4 border-b border-slate-800 shrink-0 bg-slate-900/30">
    <div class="flex items-center gap-3 min-w-0">
      <div class="p-1.5 bg-indigo-500/20 rounded-md shrink-0">
        <BrainCircuit class="w-4 h-4 text-indigo-400" />
      </div>
      <div class="flex flex-col min-w-0">
        <h2 class="text-sm font-mono font-semibold text-slate-200 leading-tight">Long-Term Vault (LanceDB)</h2>
        <span class="text-[10px] text-slate-500 font-mono truncate">Newest first</span>
      </div>
      <span class="ml-1 px-2 py-0.5 rounded-full bg-slate-800 text-[10px] text-slate-300 font-mono border border-slate-700 whitespace-nowrap shrink-0">
        {memories.length}{#if total !== null} of {total}{/if} memories
      </span>
    </div>
    <button onclick={() => loadVault(true)} disabled={isLoading} class="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-md text-xs font-mono whitespace-nowrap transition-colors">
      <RefreshCw class="w-3.5 h-3.5 {isLoading ? 'animate-spin' : ''}" />
      Refresh
    </button>
  </div>

  {#if errorMsg}
    <div class="flex items-start justify-between gap-3 px-4 py-2 bg-rose-500/10 border-b border-rose-500/30 text-rose-300 text-xs font-mono shrink-0" role="alert">
      <span class="break-words min-w-0">{errorMsg}</span>
      <button onclick={() => errorMsg = ''} class="p-1 shrink-0 text-rose-300 hover:text-rose-100 hover:bg-rose-500/20 rounded transition-colors" aria-label="Dismiss error">
        <X class="w-3.5 h-3.5" />
      </button>
    </div>
  {/if}

  <div class="flex-1 overflow-y-auto overflow-x-hidden p-2 md:p-6 space-y-3 md:space-y-4">
    {#if memories.length === 0}
      <div class="py-12 flex flex-col items-center justify-center text-slate-500 font-mono text-xs border-2 border-dashed border-slate-800 rounded-xl text-center px-4">
        <BrainCircuit class="w-8 h-8 opacity-20 mb-3" />
        {isLoading ? 'Loading the vault...' : errorMsg ? 'The vault could not be loaded.' : 'No memories in the vault yet.'}
      </div>
    {/if}

    {#each memories as mem (mem.id)}
      <div class="p-3 md:p-4 rounded-xl border border-slate-800 bg-slate-900/40 flex flex-col gap-3 group">
        <div class="flex items-start justify-between gap-3">
          <p class="text-xs font-mono text-slate-300 leading-relaxed flex-1 min-w-0 break-words">{mem.child_vector_text}</p>
          <div class="flex items-center gap-1 opacity-60 group-hover:opacity-100 transition-opacity shrink-0">
            <button onclick={() => startEdit(mem)} class="p-1.5 text-slate-500 hover:text-indigo-400 hover:bg-indigo-500/20 rounded" title="Edit Metadata" aria-label="Edit memory metadata"><Edit2 class="w-3.5 h-3.5" /></button>
            <button onclick={() => deleteMemory(mem)} class="p-1.5 text-slate-500 hover:text-rose-400 hover:bg-rose-500/20 rounded" title="Purge Memory" aria-label="Permanently delete memory"><Trash2 class="w-3.5 h-3.5" /></button>
          </div>
        </div>

        {#if expandedId === mem.id}
          <p class="text-xs font-mono text-slate-400 leading-relaxed whitespace-pre-wrap break-words p-3 bg-slate-950/50 rounded-lg border border-slate-800">{mem.raw_parent_payload}</p>
        {/if}

        {#if editingId === mem.id}
          <div class="grid grid-cols-1 md:grid-cols-3 gap-3 p-3 bg-slate-950/50 rounded-lg border border-slate-800">
            <div class="flex flex-col gap-1.5">
              <label class="text-[9px] font-mono text-slate-500 uppercase">
                Topic Tags (CSV)
                <input type="text" bind:value={editForm.topic_tags} class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs font-mono text-slate-300 outline-none focus:border-indigo-500" />
              </label>
            </div>
            <div class="flex flex-col gap-1.5">
              <label class="text-[9px] font-mono text-slate-500 uppercase">
                Tone
                {#if approvedTones.length > 0}
                  <select bind:value={editForm.emotional_tone} class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs font-mono text-slate-300 outline-none focus:border-indigo-500">
                    {#if !approvedTones.includes(editForm.emotional_tone)}
                      <option value={editForm.emotional_tone}>{editForm.emotional_tone} (not approved)</option>
                    {/if}
                    {#each approvedTones as tone}
                      <option value={tone}>{tone}</option>
                    {/each}
                  </select>
                {:else}
                  <input type="text" bind:value={editForm.emotional_tone} class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs font-mono text-slate-300 outline-none focus:border-indigo-500" />
                {/if}
              </label>
            </div>
            <div class="flex flex-col gap-1.5">
              <label class="text-[9px] font-mono text-slate-500 uppercase">
                QoS Priority (1-5)
                <input type="number" min="1" max="5" bind:value={editForm.qos_priority_weight} class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs font-mono text-slate-300 outline-none focus:border-indigo-500" />
              </label>
            </div>
            <div class="col-span-1 md:col-span-3 flex justify-end gap-2 mt-1">
              <button onclick={() => editingId = null} class="px-3 py-1 text-[10px] text-slate-400 hover:text-slate-200 uppercase font-bold">Cancel</button>
              <button onclick={() => saveMetadata(mem)} disabled={isSaving} class="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded text-[10px] uppercase font-bold flex items-center gap-1"><Save class="w-3 h-3" /> {isSaving ? 'Saving...' : 'Save'}</button>
            </div>
          </div>
        {:else}
          <div class="flex flex-wrap items-center gap-2">
            <span class="text-[10px] font-mono text-slate-500">{formatDate(mem.unix_start)}</span>
            <span class="px-1.5 py-0.5 bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 rounded text-[9px] font-mono flex items-center gap-1"><Activity class="w-2.5 h-2.5" /> QoS {mem.qos_priority_weight || 3}</span>
            <span class="px-1.5 py-0.5 bg-slate-800 text-slate-400 border border-slate-700 rounded text-[9px] font-mono">{mem.emotional_tone}</span>
            {#if mem.interaction_type === 'fictional_roleplay'}
              <span class="px-1.5 py-0.5 bg-purple-500/10 text-purple-400 border border-purple-500/20 rounded text-[9px] font-mono">roleplay</span>
            {/if}
            {#if inChain(mem)}
              <span class="px-1.5 py-0.5 bg-slate-800 text-slate-400 border border-slate-700 rounded text-[9px] font-mono" title="One part of a longer, split conversation">part of a chain</span>
            {/if}
            {#each (mem.topic_tags || []) as tag}
              <span class="px-1.5 py-0.5 rounded text-[9px] font-mono flex items-center gap-1 border {isPending(tag) ? 'bg-amber-500/10 text-amber-400 border-amber-500/30' : 'bg-sky-500/10 text-sky-400 border-sky-500/20'}"><Tag class="w-2.5 h-2.5" /> {tag}</span>
            {/each}
            {#if mem.tone_vector_proposed}
              <span class="px-1.5 py-0.5 bg-amber-500/10 text-amber-400 border border-amber-500/30 rounded text-[9px] font-mono" title="Tone the tagger proposed that isn't in the approved list">proposed tone: {mem.tone_vector_proposed}</span>
            {/if}
            {#if mem.custom_tag_proposed}
              <span class="px-1.5 py-0.5 bg-amber-500/10 text-amber-400 border border-amber-500/30 rounded text-[9px] font-mono" title="New tag the tagger proposed for review">proposed tag: {mem.custom_tag_proposed}</span>
            {/if}
            {#if mem.raw_parent_payload}
              <button onclick={() => expandedId = expandedId === mem.id ? null : mem.id} class="ml-auto px-2 py-0.5 text-[10px] font-mono text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded transition-colors">
                {expandedId === mem.id ? 'Hide full text' : 'Show full text'}
              </button>
            {/if}
          </div>
        {/if}
      </div>
    {/each}

    {#if hasMore}
      <button
        onclick={() => loadVault(false)}
        disabled={isLoading}
        class="w-full py-2 rounded-lg border border-slate-800 bg-slate-900/40 text-xs font-mono text-slate-300 hover:bg-slate-800 disabled:opacity-50 transition-colors"
      >
        {isLoading ? 'Loading...' : 'Load older memories'}
      </button>
    {/if}
  </div>
</div>
