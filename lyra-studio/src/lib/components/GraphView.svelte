<script lang="ts">
  import { onMount } from 'svelte';
  import { Network, RefreshCw, Link as LinkIcon, Box, X } from 'lucide-svelte';
  import { memory, errorText } from '../api';

  let edges = $state<any[]>([]);
  let nodesCount = $state(0);
  let isLoading = $state(false);
  let errorMsg = $state('');
  let filterText = $state('');

  // Typing "pending" here lists every relationship still waiting for review
  let visibleEdges = $derived.by(() => {
    const needle = filterText.trim().toLowerCase();
    if (!needle) return edges;
    return edges.filter((e) => `${e.from} ${e.label} ${e.to}`.toLowerCase().includes(needle));
  });

  function isPending(value: string | null | undefined) {
    return String(value ?? '').startsWith('pending:');
  }

  async function loadGraph() {
    isLoading = true;
    try {
      const data = await memory('/memories/graph/all');
      nodesCount = data.nodes?.length || 0;

      // The engine returns one edge per memory that mentions a relationship.
      // Fold repeats into a single card with a count.
      const merged = new Map<string, any>();
      for (const e of data.edges ?? []) {
        const key = `${e.from}\u0000${e.label}\u0000${e.to}`;
        const hit = merged.get(key);
        if (hit) hit.count += 1;
        else merged.set(key, { from: e.from, to: e.to, label: e.label, key, count: 1 });
      }
      edges = [...merged.values()].sort(
        (a, b) => b.count - a.count || String(a.from).localeCompare(String(b.from))
      );
      errorMsg = '';
    } catch (err) {
      errorMsg = `Could not load the graph: ${errorText(err)}`;
    }
    isLoading = false;
  }

  onMount(() => {
    loadGraph();
  });
</script>

<div class="flex flex-col h-full bg-slate-950">
  <div class="flex flex-wrap items-center justify-between gap-3 p-3 md:p-4 border-b border-slate-800 shrink-0 bg-slate-900/30">
    <div class="flex items-center gap-3 min-w-0">
      <div class="p-1.5 bg-fuchsia-500/20 rounded-md shrink-0">
        <Network class="w-4 h-4 text-fuchsia-400" />
      </div>
      <div class="flex flex-col min-w-0">
        <h2 class="text-sm font-mono font-semibold text-slate-200 leading-tight">Knowledge Graph</h2>
        <span class="text-[10px] text-slate-500 font-mono truncate">{nodesCount} entities, {edges.length} relationships</span>
      </div>
    </div>
    <div class="flex items-center gap-2 w-full sm:w-auto">
      <input
        type="search"
        bind:value={filterText}
        placeholder="Filter by entity or relationship"
        aria-label="Filter relationships"
        class="flex-1 sm:w-64 min-w-0 bg-slate-900 border border-slate-700 rounded-md px-2 py-1.5 text-xs font-mono text-slate-200 outline-none focus:border-fuchsia-500"
      />
      <button onclick={loadGraph} disabled={isLoading} class="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-md text-xs font-mono whitespace-nowrap transition-colors shrink-0">
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

  <div class="flex-1 overflow-y-auto overflow-x-hidden p-2 md:p-6">
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 md:gap-4">
      {#each visibleEdges as edge (edge.key)}
        <div class="p-3 bg-slate-900/50 border border-slate-800 rounded-lg flex flex-col gap-2 shadow-inner">
          <div class="flex items-center justify-between gap-2 md:gap-3">
            <div class="flex flex-col items-center flex-1 min-w-0 bg-slate-950 p-2 rounded border border-slate-800/80">
              <Box class="w-3.5 h-3.5 text-slate-500 mb-1" />
              <span class="text-[10px] font-mono text-center break-all {isPending(edge.from) ? 'text-amber-400' : 'text-sky-400'}">{edge.from}</span>
            </div>

            <div class="flex flex-col items-center shrink-0 max-w-[35%]">
              <span class="text-[8px] font-mono uppercase tracking-widest font-bold mb-0.5 text-center break-all {isPending(edge.label) ? 'text-amber-400' : 'text-fuchsia-500'}">{edge.label}</span>
              <LinkIcon class="w-3 h-3 text-slate-600" />
            </div>

            <div class="flex flex-col items-center flex-1 min-w-0 bg-slate-950 p-2 rounded border border-slate-800/80">
              <Box class="w-3.5 h-3.5 text-slate-500 mb-1" />
              <span class="text-[10px] font-mono text-center break-all {isPending(edge.to) ? 'text-amber-400' : 'text-emerald-400'}">{edge.to}</span>
            </div>
          </div>
          {#if edge.count > 1}
            <span class="text-[10px] font-mono text-slate-500 text-center">in {edge.count} memories</span>
          {/if}
        </div>
      {/each}

      {#if visibleEdges.length === 0 && !isLoading}
        <div class="col-span-full py-12 flex flex-col items-center justify-center text-slate-500 font-mono text-xs border-2 border-dashed border-slate-800 rounded-xl text-center px-4">
          <Network class="w-8 h-8 opacity-20 mb-3" />
          {#if errorMsg}
            The graph could not be loaded.
          {:else if edges.length > 0}
            No relationships match that filter.
          {:else}
            No graph relationships extracted yet.
          {/if}
        </div>
      {/if}
    </div>
  </div>
</div>
