<script lang="ts">
  import type { TerminalState } from '../terminal.svelte';
  import { X, Code, Copy, Check, Download, Layers, ImageOff, ExternalLink, RefreshCw, Eye, EyeOff } from 'lucide-svelte';
  import { resolveImageSrc, describeImageProblem, itemToJson } from '../media';

  interface Props {
    terminal: TerminalState;
  }

  let { terminal }: Props = $props();

  let activeTab = $state<'preview' | 'raw'>('preview');
  let copied = $state(false);

  const active = $derived(terminal.activeWorkspaceItem);
  const imageSrc = $derived(active && active.type === 'image' ? resolveImageSrc(active) : null);

  // The address that failed to load. Keyed by address, so selecting another item starts fresh.
  let failedSrc = $state<string | null>(null);
  const imageFailed = $derived(!!imageSrc && failedSrc === imageSrc);
  const imageProblem = $derived(
    imageSrc ? describeImageProblem(imageSrc, typeof location !== 'undefined' ? location.hostname : '') : ''
  );
  const isWebAddress = (s: string) => /^https?:\/\//i.test(s);
  const shortAddress = (s: string) => (s.length > 90 ? `${s.slice(0, 60)}\u2026${s.slice(-24)}` : s);

  // "Use as profile picture" for the image on screen
  let avatarBusy = $state(false);
  let avatarNote = $state<{ ok: boolean; text: string } | null>(null);
  $effect(() => {
    void active?.id; // a different item: clear the last result
    avatarNote = null;
  });
  async function useAsAvatar() {
    if (!imageSrc || avatarBusy) return;
    avatarBusy = true;
    avatarNote = null;
    const result = await terminal.setActiveAvatarFromUrl(imageSrc);
    avatarNote = { ok: result.ok, text: result.message };
    avatarBusy = false;
  }

  // navigator.clipboard only exists on secure pages (https or localhost), and this app is often opened over plain http.
  async function copyToClipboard(text: string): Promise<boolean> {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch {
      /* fall through to the older method */
    }
    try {
      const box = document.createElement('textarea');
      box.value = text;
      box.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
      document.body.appendChild(box);
      box.focus();
      box.select();
      const ok = document.execCommand('copy');
      box.remove();
      return ok;
    } catch {
      return false;
    }
  }

  async function copyText() {
    if (!active) return;
    let text: string;
    if (activeTab === 'raw') text = itemToJson(active, false);
    else if (active.type === 'image') text = active.url || (imageSrc && !imageSrc.startsWith('data:') ? imageSrc : '');
    else text = active.content || active.url || '';
    if (!text || !(await copyToClipboard(text))) return;
    copied = true;
    setTimeout(() => (copied = false), 2000);
  }

  function downloadArtifact() {
    if (!active) return;

    // Images: hand the browser the image itself (a saved text file containing the address is no use).
    if (active.type === 'image' && imageSrc) {
      const link = document.createElement('a');
      link.href = imageSrc;
      link.download = active.title || 'image';
      link.target = '_blank';
      link.rel = 'noopener';
      document.body.appendChild(link);
      link.click();
      link.remove();
      return;
    }

    const blob = new Blob([active.content || active.url || ''], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = active.title;
    a.click();
    // Allow browser download manager to capture stream before revoking blob URL
    setTimeout(() => {
      URL.revokeObjectURL(url);
    }, 1500);
  }
</script>

<aside
  class="w-80 sm:w-96 bg-slate-950 border-l border-slate-800 flex flex-col h-full z-40 shrink-0 shadow-2xl transition-all overflow-hidden"
>
  <!-- Panel Top Bar -->
  <div class="flex items-center justify-between px-4 py-3 border-b border-slate-800 bg-slate-900/70 shrink-0">
    <div class="flex items-center gap-2">
      <Layers class="w-4 h-4 text-sky-400" />
      <span class="text-xs font-semibold font-mono text-slate-200">Workspace Inspector</span>
      {#if terminal.workspaceItems.length > 0}
        <span class="px-1.5 py-0.5 rounded bg-sky-500/20 text-sky-300 text-[10px] font-mono">
          {terminal.workspaceItems.length}
        </span>
      {/if}
    </div>

    <div class="flex items-center gap-1.5">
      <button
        onclick={() => terminal.setAutoOpenViewed(!terminal.autoOpenViewed)}
        class="p-2 rounded-lg transition-colors {terminal.autoOpenViewed ? 'text-sky-300 bg-sky-500/10' : 'text-slate-500 hover:text-slate-300 hover:bg-slate-800'}"
        title={terminal.autoOpenViewed ? 'Opens by itself when the AI views something (on)' : 'Opens by itself when the AI views something (off)'}
      >
        {#if terminal.autoOpenViewed}<Eye class="w-4 h-4" />{:else}<EyeOff class="w-4 h-4" />{/if}
      </button>
      <button
        onclick={() => terminal.toggleWorkspace(false)}
        class="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition-colors"
        title="Close Inspector"
      >
        <X class="w-4 h-4" />
      </button>
    </div>
  </div>

  <!-- Artifact Selector Pills -->
  {#if terminal.workspaceItems.length > 1}
    <div class="flex items-center gap-1.5 px-3 py-2 border-b border-slate-800/80 bg-slate-900/40 overflow-x-auto no-scrollbar shrink-0">
      {#each terminal.workspaceItems as item}
        <button
          onclick={() => terminal.selectWorkspaceItem(item)}
          class="px-2.5 py-1 rounded-md text-[11px] font-mono whitespace-nowrap transition-all border {terminal.activeWorkspaceItem?.id === item.id
            ? 'bg-sky-500/20 text-sky-300 border-sky-500/40 font-medium'
            : 'bg-slate-950 text-slate-400 border-slate-800 hover:text-slate-200'}"
        >
          {#if item.source === 'viewed'}<Eye class="w-3 h-3 inline -mt-0.5 mr-1" />{/if}{item.title || 'Untitled'}
        </button>
      {/each}
    </div>
  {/if}

   <!-- Tab Toggle & Controls -->
  {#if terminal.activeWorkspaceItem}
    <div class="flex items-center justify-between px-4 py-2 border-b border-slate-800/60 bg-slate-950 text-xs shrink-0">
      
      <!-- NEW: Filepath Display -->
      <div class="flex flex-col gap-1">
        <div class="font-mono text-[10px] text-slate-500 break-all line-clamp-2">
          {terminal.activeWorkspaceItem.filepath || '/virtual/memory/buffer'}
        </div>
        <div class="flex items-center bg-slate-900 p-0.5 rounded-lg border border-slate-800 w-fit">
          <button
            onclick={() => (activeTab = 'preview')}
            class="px-3 py-1.5 rounded text-[11px] font-mono transition-colors {activeTab === 'preview'
              ? 'bg-slate-800 text-sky-300 font-medium'
              : 'text-slate-400 hover:text-slate-200'}"
          >
            Preview
          </button>
          <button
            onclick={() => (activeTab = 'raw')}
            class="px-3 py-1.5 rounded text-[11px] font-mono transition-colors {activeTab === 'raw'
              ? 'bg-slate-800 text-sky-300 font-medium'
              : 'text-slate-400 hover:text-slate-200'}"
          >
            Raw
          </button>
        </div>
      </div>

      <div class="flex items-center gap-1">
        <button
          onclick={copyText}
          class="p-2 text-slate-400 hover:text-slate-200 hover:bg-slate-900 rounded transition-colors"
          title="Copy"
        >
          {#if copied}
            <Check class="w-3.5 h-3.5 text-emerald-400" />
          {:else}
            <Copy class="w-3.5 h-3.5" />
          {/if}
        </button>
        <button
          onclick={downloadArtifact}
          class="p-2 text-slate-400 hover:text-slate-200 hover:bg-slate-900 rounded transition-colors"
          title="Download"
        >
          <Download class="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  {/if}

  <!-- Body Content -->
  <div class="flex-1 overflow-y-auto p-4 font-mono text-xs">
    {#if !terminal.activeWorkspaceItem}
      <div class="h-full flex flex-col items-center justify-center text-slate-500 gap-2 select-none text-center">
        <Code class="w-8 h-8 text-slate-600 stroke-[1.5]" />
        <p class="text-xs">No active artifact emitted.</p>
        <p class="text-[11px] text-slate-600">When the model generates code, specs, or images, they render here.</p>
      </div>
    {:else if activeTab === 'preview'}
      {#if active && active.type === 'image'}
        {#if !imageSrc}
          <div class="p-4 rounded-xl border border-amber-500/30 bg-amber-500/5 space-y-3 text-slate-300 font-sans leading-relaxed">
            <div class="flex items-center gap-2 text-amber-300 font-mono"><ImageOff class="w-4 h-4 shrink-0" /><span>No image data in this item</span></div>
            <p>The item is marked as an image, but it has no address or image data the browser can show.</p>
            <button onclick={() => (activeTab = 'raw')} class="px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-slate-200 font-mono text-[11px]">View raw data</button>
          </div>
        {:else if imageFailed}
          <div class="p-4 rounded-xl border border-rose-500/30 bg-rose-500/5 space-y-3 text-slate-300 font-sans leading-relaxed">
            <div class="flex items-center gap-2 text-rose-300 font-mono"><ImageOff class="w-4 h-4 shrink-0" /><span>Couldn't load this image</span></div>
            <p>{imageProblem}</p>
            <p class="font-mono text-[10px] text-slate-500 break-all">{shortAddress(imageSrc)}</p>
            <div class="flex flex-wrap gap-2">
              <button onclick={() => (failedSrc = null)} class="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-slate-200 font-mono text-[11px]"><RefreshCw class="w-3.5 h-3.5" /><span>Retry</span></button>
              {#if isWebAddress(imageSrc)}
                <a href={imageSrc} target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-slate-200 font-mono text-[11px]"><ExternalLink class="w-3.5 h-3.5" /><span>Open link</span></a>
              {/if}
              <button onclick={() => (activeTab = 'raw')} class="px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-slate-200 font-mono text-[11px]">View raw data</button>
            </div>
          </div>
        {:else}
          <div class="space-y-3">
            <div class="rounded-xl overflow-hidden border border-slate-800 bg-slate-900">
              <img src={imageSrc} alt={active.title} onerror={() => (failedSrc = imageSrc)} class="w-full h-auto object-contain" />
            </div>
            <p class="text-[11px] text-slate-400 text-center">{active.title}</p>
            <button
              onclick={useAsAvatar}
              disabled={avatarBusy}
              class="w-full px-3 py-2.5 rounded-lg bg-slate-900 border border-slate-700 text-slate-200 font-mono text-[11px] hover:bg-slate-800 disabled:opacity-60"
            >
              {avatarBusy ? 'Saving\u2026' : `Use as ${terminal.characterName}'s picture`}
            </button>
            {#if avatarNote}
              <p class="text-[11px] leading-relaxed font-sans {avatarNote.ok ? 'text-emerald-400' : 'text-rose-400'}">{avatarNote.text}</p>
            {/if}
          </div>
        {/if}
      {:else}
        {#if active?.url && isWebAddress(active.url)}
          <a href={active.url} target="_blank" rel="noopener noreferrer" class="mb-3 inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-sky-300 font-mono text-[11px]"><ExternalLink class="w-3.5 h-3.5" /><span>Open the page</span></a>
        {/if}
        <div class="p-3.5 bg-slate-900/80 rounded-xl border border-slate-800 leading-relaxed text-slate-200 whitespace-pre-wrap font-sans">
          {active?.content ?? ''}
        </div>
      {/if}
    {:else}
      <!-- Raw Data Inspector: exactly what the gateway sent (long values are shortened here; Copy gives the full data) -->
      <pre class="p-3.5 bg-slate-900/80 rounded-xl border border-slate-800 text-[11px] text-sky-200 overflow-x-auto whitespace-pre-wrap break-all">{itemToJson(active)}</pre>
      <p class="mt-2 text-[10px] text-slate-600 font-sans">Long values are shortened in this view. Copy gives the full data.</p>
    {/if}
  </div>

  <!-- Footer Info -->
  {#if terminal.activeWorkspaceItem}
    <div class="px-4 py-2 border-t border-slate-800/80 bg-slate-900/50 text-[10px] font-mono text-slate-500 flex justify-between">
      <span>{terminal.activeWorkspaceItem.source === 'viewed' ? 'VIEWED \u00b7 ' : terminal.activeWorkspaceItem.source === 'created' ? 'CREATED \u00b7 ' : ''}{String(terminal.activeWorkspaceItem.type ?? 'text').toUpperCase()}</span>
      <span>{isNaN(new Date(terminal.activeWorkspaceItem.timestamp).getTime()) ? '' : new Date(terminal.activeWorkspaceItem.timestamp).toLocaleTimeString()}</span>
    </div>
  {/if}
</aside>
