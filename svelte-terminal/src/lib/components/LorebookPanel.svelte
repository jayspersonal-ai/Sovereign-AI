<!-- src/lib/components/LorebookPanel.svelte -->
<script lang="ts">
  import type { TerminalState } from '../terminal.svelte';
  import type { LoreNode, LoreFolder } from '../types';
  import SaveBar from './SaveBar.svelte';
  import { Folder, FolderOpen, FileText, Plus, Trash2, BookOpen, ChevronRight, ChevronDown, ChevronLeft } from 'lucide-svelte';

  interface Props {
    terminal: TerminalState;
  }

  let { terminal }: Props = $props();

  // The lorebook currently being edited (one file on the data server).
  const doc = $derived(terminal.activeLorebookDoc);

  // Phones show one pane at a time (page list, or the editor); md+ screens show both side by side.
  let mobilePane = $state<'tree' | 'editor'>('tree');
  function openPage(id: string) {
    terminal.activeLorePageId = id;
    mobilePane = 'editor';
  }

  // Folder open/closed is a per-device preference, so it lives here and not in the saved file.
  const EXPANDED_KEY = 'lore-expanded';
  function readExpanded(): Record<string, boolean> {
    try { return JSON.parse(localStorage.getItem(EXPANDED_KEY) ?? '{}'); } catch { return {}; }
  }
  let expanded = $state<Record<string, boolean>>(readExpanded());
  $effect(() => {
    try { localStorage.setItem(EXPANDED_KEY, JSON.stringify(expanded)); } catch { /* ignore */ }
  });

  function addNode(type: 'folder' | 'page', parent?: LoreFolder) {
    if (!doc) return;
    const id = `${type === 'folder' ? 'f' : 'p'}-${Date.now()}`;
    const node: LoreNode = type === 'folder'
      ? { id, type: 'folder', title: 'New Folder', children: [] }
      : { id, type: 'page', title: 'New Page', content: '', isActive: true };
    (parent ? parent.children : doc.data.nodes).push(node);
    if (parent) expanded[parent.id] = true;
    if (type === 'folder') expanded[id] = true;
    else openPage(id);
  }

  function removeNode(list: LoreNode[], id: string): boolean {
    const i = list.findIndex((n) => n.id === id);
    if (i !== -1) {
      list.splice(i, 1);
      return true;
    }
    for (const n of list) {
      if (n.type === 'folder' && removeNode(n.children, id)) return true;
    }
    return false;
  }

  // Deletes only change the lorebook in memory until you press Save (Revert brings them back).
  function deleteNode(id: string) {
    if (!doc) return;
    removeNode(doc.data.nodes, id);
    if (terminal.activeLorePageId === id) terminal.activeLorePageId = null;
  }

  function deleteLorebook() {
    if (confirm(`Delete lorebook "${doc?.data.name}"? This removes its file.`)) terminal.deleteActiveLorebook();
  }
</script>

<div class="flex flex-1 w-full h-full min-w-0 min-h-0 bg-slate-950 text-slate-200 md:divide-x md:divide-slate-800/60 font-mono">

  <!-- LEFT SIDEBAR: LOREBOOK PICKER + FOLDER/FILE TREE -->
  <div class="{mobilePane === 'editor' ? 'hidden' : 'flex'} md:flex w-full md:w-72 flex-col h-full min-h-0 bg-slate-900/20 select-none shrink-0">
    <div class="p-3 border-b border-slate-800/80 bg-slate-900/40 flex items-center justify-between text-xs">
      <div class="flex items-center gap-2 font-semibold text-slate-300">
        <BookOpen class="w-3.5 h-3.5 text-amber-400" />
        <span>LORE_REPOS</span>
      </div>
      <div class="flex items-center gap-1">
        <button onclick={() => addNode('folder')} class="p-2 md:p-1 hover:bg-slate-800 hover:text-amber-400 rounded text-slate-400" title="Add New Folder">
          <Folder class="w-3.5 h-3.5" />
        </button>
        <button onclick={() => addNode('page')} class="p-2 md:p-1 hover:bg-slate-800 hover:text-sky-400 rounded text-slate-400" title="Add New Lore Page">
          <Plus class="w-3.5 h-3.5" />
        </button>
      </div>
    </div>

    <!-- Lorebook picker -->
    <div class="px-3 py-2 border-b border-slate-800/40 bg-slate-950 space-y-2 text-[11px]">
      <div class="flex items-center gap-1.5">
        <select bind:value={terminal.activeLorebookId} class="flex-1 min-w-0 bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-200 outline-none focus:border-amber-500/60">
          {#each terminal.lorebookDocs as lb (lb.id)}
            <option value={lb.id}>{lb.data.name}</option>
          {/each}
        </select>
        <button onclick={() => terminal.createLorebook()} class="p-1.5 rounded border border-emerald-500/30 bg-emerald-500/10 text-emerald-400" title="New lorebook">
          <Plus class="w-3 h-3" />
        </button>
        <button onclick={deleteLorebook} disabled={terminal.lorebookDocs.length <= 1} class="p-1.5 rounded border border-rose-500/30 bg-rose-500/10 text-rose-400 disabled:opacity-40" title="Delete this lorebook">
          <Trash2 class="w-3 h-3" />
        </button>
      </div>
      {#if doc}
        <input type="text" bind:value={doc.data.name} placeholder="Lorebook name" class="w-full bg-slate-900 border border-slate-800 rounded px-2 py-1 text-slate-300 outline-none focus:border-amber-500/60" />
      {/if}
    </div>

    <!-- Master session toggle -->
    <div class="px-3.5 py-2 border-b border-slate-800/40 bg-slate-950 flex items-center justify-between text-[11px]">
      <span class="text-slate-400">Pipeline State:</span>
      <label class="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={terminal.activeSession?.lorebookEnabled || false}
          onchange={(e) => terminal.toggleSessionLorebook(e.currentTarget.checked)}
          class="accent-amber-500 rounded bg-slate-900"
        />
        <span class={terminal.activeSession?.lorebookEnabled ? 'text-emerald-400' : 'text-slate-500'}>
          {terminal.activeSession?.lorebookEnabled ? 'INJECTING' : 'OFFLINE'}
        </span>
      </label>
    </div>

    <!-- Tree -->
    <div class="flex-1 overflow-y-auto p-2 space-y-0.5 text-xs">
      {#snippet renderTree(nodes: LoreNode[])}
        {#each nodes as node (node.id)}
          <div class="flex flex-col">
            {#if node.type === 'folder'}
              <div class="group flex items-center justify-between px-1.5 py-2.5 md:py-1 rounded hover:bg-slate-900 text-amber-300/90">
                <button onclick={() => (expanded[node.id] = !expanded[node.id])} class="flex-1 flex items-center gap-1.5 text-left truncate">
                  {#if expanded[node.id]}
                    <ChevronDown class="w-3 h-3 text-slate-600" />
                    <FolderOpen class="w-3.5 h-3.5" />
                  {:else}
                    <ChevronRight class="w-3 h-3 text-slate-600" />
                    <Folder class="w-3.5 h-3.5" />
                  {/if}
                  <span class="truncate">{node.title}</span>
                </button>
                <div class="flex md:hidden md:group-hover:flex items-center gap-2 md:gap-1">
                  <button onclick={() => addNode('page', node)} class="p-1.5 md:p-0.5 text-slate-500 md:text-slate-600 hover:text-sky-400" title="Add page to this folder">
                    <Plus class="w-3 h-3" />
                  </button>
                  <button onclick={() => deleteNode(node.id)} class="p-1.5 md:p-0.5 text-slate-500 md:text-slate-600 hover:text-rose-400" title="Delete folder">
                    <Trash2 class="w-3 h-3" />
                  </button>
                </div>
              </div>

              {#if expanded[node.id] && node.children}
                <div class="pl-4 border-l border-slate-800/60 ml-2 mt-0.5 space-y-0.5">
                  {@render renderTree(node.children)}
                </div>
              {/if}
            {:else}
              <div class="group flex items-center justify-between px-1.5 py-2.5 md:py-1 rounded text-xs border {terminal.activeLorePageId === node.id
                ? 'bg-slate-800/80 text-sky-300 border-sky-500/20'
                : 'border-transparent text-slate-400 hover:bg-slate-900 hover:text-slate-200'}">
                <button onclick={() => openPage(node.id)} class="flex-1 flex items-center gap-2 text-left truncate">
                  <FileText class="w-3.5 h-3.5 {node.isActive ? 'text-sky-400' : 'text-slate-600'}" />
                  <span class="truncate {node.isActive ? '' : 'line-through opacity-50'}">{node.title}</span>
                </button>
                <div class="flex items-center gap-2 md:gap-1">
                  <input type="checkbox" bind:checked={node.isActive} class="accent-sky-500 w-5 h-5 md:w-3.5 md:h-3.5 cursor-pointer" title="Include this page when the lorebook is injecting" />
                  <button onclick={() => deleteNode(node.id)} class="block md:hidden md:group-hover:block p-1.5 md:p-0.5 text-slate-500 md:text-slate-600 hover:text-rose-400" title="Delete page">
                    <Trash2 class="w-3 h-3" />
                  </button>
                </div>
              </div>
            {/if}
          </div>
        {/each}
      {/snippet}

      {#if doc}
        {@render renderTree(doc.data.nodes)}
      {:else}
        <p class="px-2 py-3 text-[11px] text-slate-600">Loading lorebooks…</p>
      {/if}
    </div>

    <!-- Save + close -->
    <div class="p-2 border-t border-slate-800/80 bg-slate-900/20 space-y-2">
      <SaveBar {doc} />
      <button onclick={() => terminal.toggleLorebookView(false)} class="w-full py-1 text-[11px] bg-slate-900 hover:bg-slate-850 border border-slate-800 rounded text-slate-400 hover:text-slate-200 transition-colors">
        ✕ Close Explorer
      </button>
    </div>
  </div>

  <!-- RIGHT CANVAS: PAGE EDITOR -->
  <div class="{mobilePane === 'tree' ? 'hidden' : 'flex'} md:flex flex-1 min-w-0 h-full min-h-0 flex-col bg-slate-950 font-sans">
    <!-- Phone-only bar: back to the page list, and Save without leaving the editor -->
    <div class="md:hidden flex items-center justify-between gap-2 p-2 border-b border-slate-800/80 bg-slate-900/60 shrink-0">
      <button onclick={() => (mobilePane = 'tree')} class="flex items-center gap-1 px-2.5 py-2 rounded-lg text-xs font-mono text-slate-300 bg-slate-900 border border-slate-800 active:bg-slate-800">
        <ChevronLeft class="w-4 h-4" />
        <span>Pages</span>
      </button>
      <SaveBar {doc} />
    </div>

    {#if terminal.activeLorePage}
      {@const page = terminal.activeLorePage}
      <div class="p-3 border-b border-slate-800/80 bg-slate-900/40 flex flex-col gap-2 font-mono text-xs sm:flex-row sm:items-center sm:justify-between shrink-0">
        <div class="space-y-1 min-w-0 w-full sm:w-auto">
          <div class="text-[10px] text-slate-500 uppercase tracking-wide">Editing Artifact Target</div>
          <input type="text" bind:value={page.title} class="bg-slate-950 border border-slate-800 rounded px-2 py-1.5 sm:py-1 text-xs text-slate-200 outline-none focus:border-sky-500/60 font-mono w-full sm:w-64" />
        </div>
        <div class="text-[10px] text-slate-500 text-left sm:text-right font-mono">
          Size: {page.content.length} chars • ~{Math.max(1, Math.round(page.content.length / 3.8))} tokens
        </div>
      </div>

      <div class="flex-1 min-h-0 p-3 sm:p-6 bg-slate-950">
        <textarea
          bind:value={page.content}
          class="w-full h-full bg-slate-900/40 text-slate-100 p-4 border border-slate-800/80 rounded-xl font-mono text-xs leading-relaxed outline-none resize-none focus:border-sky-500/30 shadow-inner"
          placeholder="# Write description details here..."
        ></textarea>
      </div>
    {:else}
      <div class="flex-1 flex flex-col items-center justify-center text-slate-500 gap-2 select-none font-mono">
        <FileText class="w-8 h-8 text-slate-700 stroke-[1.5]" />
        <p class="text-xs">No repository node selected.</p>
        <p class="text-[11px] text-slate-600">Select a file from the explorer pane to open editing buffer.</p>
      </div>
    {/if}
  </div>
</div>
