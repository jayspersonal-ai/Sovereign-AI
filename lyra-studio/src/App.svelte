<script lang="ts">
  import { Database, Network, BrainCircuit } from 'lucide-svelte';
  import PostgresView from './lib/components/PostgresView.svelte';
  import VaultView from './lib/components/VaultView.svelte';
  import GraphView from './lib/components/GraphView.svelte';

  let activeTab = $state('postgres');

  const tabBase =
    'flex-1 md:flex-none md:w-full flex flex-col md:flex-row items-center justify-center md:justify-start gap-1 md:gap-3 px-2 py-2 md:p-3 rounded-lg font-mono text-[11px] md:text-xs border transition-colors';
  const tabIdle = 'border-transparent text-slate-400 hover:bg-slate-800/50 hover:text-slate-200';
</script>

<!-- Phones: content on top, navigation bar along the bottom.
     Wider screens: navigation sidebar on the left. -->
<div class="flex flex-col md:flex-row h-full w-full overflow-hidden bg-slate-950 text-slate-200 font-sans">
  <aside class="order-last md:order-first w-full md:w-64 bg-slate-900 border-t md:border-t-0 md:border-r border-slate-800 flex md:flex-col shrink-0">
    <div class="hidden md:flex p-4 border-b border-slate-800 items-center gap-3">
      <div class="w-8 h-8 bg-sky-500/20 text-sky-400 flex items-center justify-center rounded-lg shadow-inner shrink-0">
        <Database class="w-4 h-4" />
      </div>
      <span class="font-mono font-bold text-sm text-slate-100 truncate">Lyra Studio</span>
    </div>

    <nav class="flex-1 flex md:flex-col gap-1 p-1.5 md:p-2">
      <button
        onclick={() => activeTab = 'postgres'}
        class="{tabBase} {activeTab === 'postgres' ? 'bg-sky-600/20 text-sky-400 border-sky-500/30' : tabIdle}"
      >
        <Database class="w-4 h-4 shrink-0" />
        <span class="md:hidden">SQL</span>
        <span class="hidden md:inline truncate">Short-Term (SQL)</span>
      </button>

      <button
        onclick={() => activeTab = 'vault'}
        class="{tabBase} {activeTab === 'vault' ? 'bg-emerald-600/20 text-emerald-400 border-emerald-500/30' : tabIdle}"
      >
        <BrainCircuit class="w-4 h-4 shrink-0" />
        <span class="md:hidden">Vault</span>
        <span class="hidden md:inline truncate">Vault (LanceDB)</span>
      </button>

      <button
        onclick={() => activeTab = 'graph'}
        class="{tabBase} {activeTab === 'graph' ? 'bg-purple-600/20 text-purple-400 border-purple-500/30' : tabIdle}"
      >
        <Network class="w-4 h-4 shrink-0" />
        <span class="md:hidden">Graph</span>
        <span class="hidden md:inline truncate">Knowledge Graph</span>
      </button>
    </nav>
  </aside>

  <!-- Data Viewport -->
  <div class="flex-1 min-h-0 min-w-0 p-2 md:p-6 overflow-hidden flex flex-col">
    {#if activeTab === 'postgres'}
      <PostgresView />
    {:else if activeTab === 'vault'}
      <VaultView />
    {:else if activeTab === 'graph'}
      <GraphView />
    {/if}
  </div>
</div>
