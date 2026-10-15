<script lang="ts">
  import { onMount } from 'svelte';
  import { RefreshCw, Edit2, Trash2, Save, X, Database, History } from 'lucide-svelte';
  import ChatLogsView from './ChatLogsView.svelte';
  import { stm, errorText } from '../api';

  // Svelte 5 State Runes
  let subTab = $state('spotlights'); // 'spotlights' or 'logs'

  let spotlights = $state<any[]>([]);
  let isLoading = $state(false);
  let isSaving = $state(false);
  let errorMsg = $state(''); // problems loading or deleting, shown above the table
  let formError = $state(''); // problems saving, shown inside the modal

  // Modal state: null = closed, 'new' = creating, a number = editing that record
  let editingId = $state<number | 'new' | null>(null);
  let editForm = $state<any>({});

  async function loadData() {
    isLoading = true;
    try {
      spotlights = await stm('/v1/spotlight/browse');
      errorMsg = '';
    } catch (err) {
      errorMsg = `Could not load spotlights: ${errorText(err)}`;
    }
    isLoading = false;
  }

  async function deleteSpotlight(id: number) {
    if (!confirm('Permanently delete this spotlight?')) return;
    try {
      await stm(`/v1/spotlight/${id}`, 'DELETE');
      spotlights = spotlights.filter((s) => s.id !== id);
    } catch (err) {
      errorMsg = `Could not delete spotlight: ${errorText(err)}`;
    }
  }

  function startCreate() {
    editForm = { title: '', tone_tags: '', child_summary: '', parent_data: '', why_cherished: '' };
    formError = '';
    editingId = 'new';
  }

  async function startEdit(spotlight: any) {
    try {
      // Plain read: does not count as Lyra recalling the memory
      const detail = await stm(`/v1/spotlight/${spotlight.id}`);
      editForm = {
        title: detail.title ?? '',
        tone_tags: (detail.tone_tags ?? []).join(', '),
        child_summary: detail.child_summary ?? '',
        parent_data: detail.parent_data ?? '',
        why_cherished: detail.why_cherished ?? ''
      };
      formError = '';
      editingId = spotlight.id;
    } catch (err) {
      errorMsg = `Could not open spotlight: ${errorText(err)}`;
    }
  }

  function closeModal() {
    editingId = null;
    formError = '';
  }

  async function saveEdit() {
    const payload = {
      title: String(editForm.title ?? '').trim(),
      child_summary: String(editForm.child_summary ?? '').trim(),
      parent_data: String(editForm.parent_data ?? ''),
      why_cherished: String(editForm.why_cherished ?? '').trim() || null,
      // The form holds tags as comma-separated text; the server stores a list
      tone_tags: String(editForm.tone_tags ?? '')
        .split(',')
        .map((t: string) => t.trim())
        .filter(Boolean)
    };

    if (!payload.title || !payload.child_summary || !payload.parent_data.trim()) {
      formError = 'Headline, child summary and parent transcript are all required.';
      return;
    }

    isSaving = true;
    try {
      if (editingId === 'new') {
        await stm('/v1/spotlight', 'POST', payload);
      } else {
        await stm(`/v1/spotlight/${editingId}`, 'PATCH', payload);
      }
      closeModal();
      await loadData();
    } catch (err) {
      formError = errorText(err);
    }
    isSaving = false;
  }

  onMount(() => {
    loadData();
  });
</script>

<div class="flex flex-col h-full bg-slate-950">

  <!-- Sub-Tab Navigation Header -->
  <div class="p-2 md:p-3 border-b border-slate-800 bg-slate-900/80 shrink-0">
    <div class="flex items-center gap-1 md:gap-2 bg-slate-950 p-1 rounded-lg border border-slate-800 w-full sm:w-fit shadow-inner">
      <button
        onclick={() => subTab = 'spotlights'}
        class="flex-1 sm:flex-none flex items-center justify-center gap-2 px-3 md:px-4 py-1.5 rounded-md text-xs font-mono whitespace-nowrap transition-all {subTab === 'spotlights' ? 'bg-sky-600 text-white shadow-sm' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'}"
      >
        <Database class="w-3.5 h-3.5" />
        Spotlights
      </button>
      <button
        onclick={() => subTab = 'logs'}
        class="flex-1 sm:flex-none flex items-center justify-center gap-2 px-3 md:px-4 py-1.5 rounded-md text-xs font-mono whitespace-nowrap transition-all {subTab === 'logs' ? 'bg-emerald-600 text-white shadow-sm' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'}"
      >
        <History class="w-3.5 h-3.5" />
        Chat Logs
      </button>
    </div>
  </div>

  <!-- Conditional View Rendering -->
  <div class="flex-1 min-h-0 overflow-hidden">
    {#if subTab === 'logs'}
      <ChatLogsView />
    {:else}
      <!-- Spotlights Table Layout -->
      <div class="flex flex-col h-full">
        <!-- Toolbar -->
        <div class="flex flex-wrap items-center justify-between gap-3 p-3 md:p-4 border-b border-slate-800 shrink-0 bg-slate-900/30">
          <div class="flex items-center gap-3 min-w-0">
            <div class="p-1.5 bg-sky-500/20 rounded-md shrink-0">
              <Database class="w-4 h-4 text-sky-400" />
            </div>
            <div class="flex flex-col min-w-0">
              <h2 class="text-sm font-mono font-semibold text-slate-200 leading-tight">Spotlights Index</h2>
              <span class="text-[10px] text-slate-500 font-mono truncate">short_term_memory.memory_spotlight</span>
            </div>
            <span class="ml-1 px-2 py-0.5 rounded-full bg-slate-800 text-[10px] text-slate-300 font-mono border border-slate-700 whitespace-nowrap shrink-0">
              {spotlights.length} records
            </span>
          </div>

          <div class="flex items-center gap-2">
            <button
              onclick={startCreate}
              class="px-3 py-1.5 bg-sky-600 hover:bg-sky-500 text-white rounded-md text-xs font-mono whitespace-nowrap transition-colors"
            >
              New Spotlight
            </button>
            <button
              onclick={loadData}
              class="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-md text-xs font-mono whitespace-nowrap transition-colors border border-slate-700"
              disabled={isLoading}
            >
              <RefreshCw class="w-3.5 h-3.5 {isLoading ? 'animate-spin' : ''}" />
              Refresh
            </button>
          </div>
        </div>

        {#if errorMsg}
          <div class="flex items-start justify-between gap-3 px-4 py-2 bg-rose-500/10 border-b border-rose-500/30 text-rose-300 text-xs font-mono shrink-0" role="alert">
            <span class="break-words min-w-0">{errorMsg}</span>
            <button onclick={() => errorMsg = ''} class="p-1 shrink-0 text-rose-300 hover:text-rose-100 hover:bg-rose-500/20 rounded transition-colors" aria-label="Dismiss error">
              <X class="w-3.5 h-3.5" />
            </button>
          </div>
        {/if}

        <!-- Table Container -->
        <div class="flex-1 overflow-y-auto overflow-x-hidden p-3 md:p-6">
          {#if isLoading && spotlights.length === 0}
            <div class="flex flex-col items-center justify-center h-40 text-slate-500 font-mono text-xs gap-3">
              <RefreshCw class="w-5 h-5 animate-spin text-sky-500/50" />
              Querying PostgreSQL...
            </div>
          {:else if spotlights.length === 0}
            <div class="flex flex-col items-center justify-center h-48 text-slate-500 font-mono text-xs border-2 border-dashed border-slate-800/80 rounded-xl bg-slate-900/20 text-center px-4">
              <Database class="w-6 h-6 text-slate-600 mb-2" />
              <span class="text-slate-400 font-medium">No spotlights found in PostgreSQL.</span>
              <span class="text-slate-600 mt-1">The table is ready but empty. Add a memory with New Spotlight.</span>
            </div>
          {:else}
            <!-- Phones and tablets: one card per memory, stacked vertically -->
            <div class="lg:hidden space-y-3">
              {#each spotlights as item (item.id)}
                <div class="p-3 rounded-xl border border-slate-800 bg-slate-900/40 flex flex-col gap-2">
                  <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0">
                      <div class="text-sm font-mono font-semibold text-sky-300 break-words">{item.title}</div>
                      <div class="text-[11px] font-mono text-slate-500 mt-0.5">ID {item.id}</div>
                    </div>
                    <div class="flex items-center gap-1 shrink-0">
                      <button onclick={() => startEdit(item)} class="p-2 text-slate-400 hover:text-sky-300 hover:bg-sky-500/20 rounded transition-colors" title="Edit Spotlight" aria-label="Edit spotlight">
                        <Edit2 class="w-4 h-4" />
                      </button>
                      <button onclick={() => deleteSpotlight(item.id)} class="p-2 text-slate-400 hover:text-rose-400 hover:bg-rose-500/20 rounded transition-colors" title="Purge Spotlight" aria-label="Permanently delete spotlight">
                        <Trash2 class="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                  <p class="text-xs font-mono text-slate-400 leading-relaxed break-words">{item.child_summary}</p>
                  {#if (item.tone_tags ?? []).length > 0}
                    <div class="flex flex-wrap gap-1.5">
                      {#each item.tone_tags as tag}
                        <span class="px-1.5 py-0.5 rounded bg-slate-800/80 text-[11px] font-mono text-slate-300 border border-slate-700">{tag}</span>
                      {/each}
                    </div>
                  {/if}
                </div>
              {/each}
            </div>

            <!-- Wide screens: table -->
            <div class="hidden lg:block border border-slate-800 rounded-xl overflow-hidden bg-slate-900/40 shadow-xl">
              <table class="w-full text-left text-xs font-mono text-slate-300">
                <thead class="bg-slate-900 text-slate-400 border-b border-slate-800 shadow-sm">
                  <tr>
                    <th class="px-4 py-3.5 font-medium w-16 text-center">ID</th>
                    <th class="px-4 py-3.5 font-medium w-1/4">Headline</th>
                    <th class="px-4 py-3.5 font-medium">Child Summary</th>
                    <th class="px-4 py-3.5 font-medium w-48">Tone Tags</th>
                    <th class="px-4 py-3.5 font-medium w-24 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-slate-800/50">
                  {#each spotlights as item (item.id)}
                    <tr class="hover:bg-slate-800/40 transition-colors group">
                      <td class="px-4 py-3 text-slate-500 text-center">{item.id}</td>
                      <td class="px-4 py-3 font-semibold text-sky-300 truncate max-w-[200px]" title={item.title}>{item.title}</td>
                      <td class="px-4 py-3 text-slate-400 truncate max-w-sm" title={item.child_summary}>{item.child_summary}</td>
                      <td class="px-4 py-3">
                        <div class="flex flex-wrap gap-1.5">
                          {#each (item.tone_tags ?? []) as tag}
                            <span class="px-1.5 py-0.5 rounded bg-slate-800/80 text-[9px] text-slate-300 border border-slate-700">{tag}</span>
                          {/each}
                        </div>
                      </td>
                      <td class="px-4 py-3 text-right">
                        <div class="flex items-center justify-end gap-1 opacity-60 group-hover:opacity-100 transition-opacity">
                          <button onclick={() => startEdit(item)} class="p-1.5 text-slate-400 hover:text-sky-300 hover:bg-sky-500/20 rounded transition-colors" title="Edit Spotlight" aria-label="Edit spotlight">
                            <Edit2 class="w-3.5 h-3.5" />
                          </button>
                          <button onclick={() => deleteSpotlight(item.id)} class="p-1.5 text-slate-400 hover:text-rose-400 hover:bg-rose-500/20 rounded transition-colors" title="Purge Spotlight" aria-label="Permanently delete spotlight">
                            <Trash2 class="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          {/if}
        </div>
      </div>
    {/if}
  </div>
</div>

<!-- Fixed Create / Edit Modal Overlay -->
{#if editingId !== null}
  <div class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-3 md:p-8">
    <div class="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-3xl flex flex-col shadow-2xl max-h-full">
      <!-- Header -->
      <div class="flex items-center justify-between p-4 md:p-5 border-b border-slate-800 bg-slate-800/30">
        <div class="flex items-center gap-3">
          <div class="p-1.5 bg-sky-500/20 rounded-md">
            <Edit2 class="w-4 h-4 text-sky-400" />
          </div>
          <h3 class="text-sm font-mono font-semibold text-slate-200">
            {editingId === 'new' ? 'New Spotlight Record' : `Editing Spotlight Record #${editingId}`}
          </h3>
        </div>
        <button onclick={closeModal} class="text-slate-400 hover:text-slate-200 p-1 rounded-md hover:bg-slate-700 transition-colors" aria-label="Close">
          <X class="w-4 h-4" />
        </button>
      </div>

      <!-- Scrolling Form Body -->
      <div class="p-4 md:p-6 space-y-5 overflow-y-auto font-sans bg-slate-950/30">

        {#if formError}
          <div class="px-3.5 py-2.5 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs font-mono break-words" role="alert">
            {formError}
          </div>
        {/if}

        <div class="grid grid-cols-1 md:grid-cols-2 gap-5">
          <div class="space-y-1.5">
            <label class="text-[10px] font-mono text-slate-400 uppercase tracking-wider font-semibold">
              Memory Headline
              <input type="text" maxlength="120" bind:value={editForm.title} class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3.5 py-2.5 text-sm text-sky-100 font-mono focus:outline-none focus:border-sky-500 focus:ring-1 focus:ring-sky-500/50 transition-all shadow-inner" />
            </label>
          </div>

          <div class="space-y-1.5">
            <label class="text-[10px] font-mono text-slate-400 uppercase tracking-wider font-semibold">
              Tone Tags (CSV)
              <input type="text" bind:value={editForm.tone_tags} placeholder="e.g. tender, nostalgic, milestone" class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3.5 py-2.5 text-sm text-slate-200 font-mono focus:outline-none focus:border-sky-500 focus:ring-1 focus:ring-sky-500/50 transition-all shadow-inner" />
            </label>
          </div>
        </div>

        <div class="space-y-1.5">
          <label class="text-[10px] font-mono text-slate-400 uppercase tracking-wider font-semibold">
            Child Summary (LLM Context)
            <textarea maxlength="300" bind:value={editForm.child_summary} rows="2" class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3.5 py-2.5 text-sm text-slate-200 focus:outline-none focus:border-sky-500 focus:ring-1 focus:ring-sky-500/50 transition-all shadow-inner resize-y"></textarea>
          </label>
        </div>

        <div class="space-y-1.5">
          <label class="text-[10px] font-mono text-slate-400 uppercase tracking-wider font-semibold">
            Why Cherished (Optional)
            <input type="text" maxlength="255" bind:value={editForm.why_cherished} class="w-full bg-slate-950 border border-slate-800 rounded-lg px-3.5 py-2.5 text-sm text-slate-200 focus:outline-none focus:border-sky-500 focus:ring-1 focus:ring-sky-500/50 transition-all shadow-inner" />
          </label>
        </div>

        <div class="space-y-1.5">
          <label for="spotlight-parent-data" class="text-[10px] font-mono text-slate-400 uppercase tracking-wider font-semibold flex items-center justify-between">
            <span>Verbatim Parent Transcript</span>
            <span class="text-sky-500/50 lowercase tracking-normal text-[9px]">parent_data</span>
          </label>
          <textarea id="spotlight-parent-data" bind:value={editForm.parent_data} rows="7" class="w-full font-mono text-xs bg-slate-950 border border-slate-800 rounded-lg px-3.5 py-3 text-slate-400 focus:outline-none focus:border-sky-500 focus:ring-1 focus:ring-sky-500/50 transition-all shadow-inner resize-y leading-relaxed"></textarea>
        </div>
      </div>

      <!-- Footer Actions -->
      <div class="p-4 md:p-5 border-t border-slate-800 flex items-center justify-end gap-3 bg-slate-800/30 rounded-b-xl">
        <button onclick={closeModal} class="px-4 py-2 rounded-lg text-xs font-mono font-medium text-slate-400 hover:text-slate-200 hover:bg-slate-700 transition-colors">
          Cancel
        </button>
        <button onclick={saveEdit} disabled={isSaving} class="flex items-center gap-2 px-5 py-2 rounded-lg text-xs font-mono font-bold bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-white transition-all shadow-md shadow-sky-900/20">
          <Save class="w-3.5 h-3.5" />
          {isSaving ? 'Saving...' : 'Commit to Postgres'}
        </button>
      </div>
    </div>
  </div>
{/if}
