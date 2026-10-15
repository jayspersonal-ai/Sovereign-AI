<script lang="ts">
  import type { TerminalState } from '../terminal.svelte';
  import type { ChatAttachment } from '../types';
  import { Send, Square, Paperclip, X, RefreshCw } from 'lucide-svelte';
  import { tick, onMount } from 'svelte';

  interface Props {
    terminal: TerminalState;
  }

  let { terminal }: Props = $props();

  let inputPrompt = $state('');
  let textareaElement: HTMLTextAreaElement | null = $state(null);

  // Images waiting to be sent. Each is saved to the workspace as soon as it is added, so Send is quick on a weak signal.
  const MAX_IMAGES = 4;
  interface Pending { key: number; name: string; preview: string; status: 'uploading' | 'ready' | 'error'; error?: string; attachment?: ChatAttachment }
  let pending = $state<Pending[]>([]);
  let nextKey = 0;
  let dragging = $state(false);
  let fileInput: HTMLInputElement | null = $state(null);
  let attachNotice = $state('');

  const uploading = $derived(pending.some((p) => p.status === 'uploading'));
  const readyAttachments = $derived(pending.filter((p) => p.status === 'ready' && p.attachment).map((p) => p.attachment!));
  const canSend = $derived((!!inputPrompt.trim() || readyAttachments.length > 0) && !uploading && !pending.some((p) => p.status === 'error'));

  async function addFiles(files: File[]) {
    attachNotice = '';
    const images = files.filter((f) => f.type.startsWith('image/'));
    if (files.length && !images.length) attachNotice = 'Only images can be attached.';
    for (const file of images) {
      if (pending.length >= MAX_IMAGES) { attachNotice = `Up to ${MAX_IMAGES} images per message.`; break; }
      const key = nextKey++;
      pending.push({ key, name: file.name || 'image', preview: URL.createObjectURL(file), status: 'uploading' });
      const set = (patch: Partial<Pending>) => {
        const i = pending.findIndex((p) => p.key === key);
        if (i >= 0) Object.assign(pending[i], patch);
      };
      terminal.uploadImage(file, file.name).then(
        (attachment) => set({ status: 'ready', attachment }),
        (e) => set({ status: 'error', error: e?.message || 'Upload failed.' })
      );
    }
  }

  function removePending(key: number) {
    const i = pending.findIndex((p) => p.key === key);
    if (i < 0) return;
    URL.revokeObjectURL(pending[i].preview);
    pending.splice(i, 1);
  }

  function handlePaste(e: ClipboardEvent) {
    const files = Array.from(e.clipboardData?.files ?? []).filter((f) => f.type.startsWith('image/'));
    if (!files.length) return; // plain text pastes as usual
    e.preventDefault();
    addFiles(files);
  }

  function handleDrop(e: DragEvent) {
    e.preventDefault();
    dragging = false;
    addFiles(Array.from(e.dataTransfer?.files ?? []));
  }

  function handleFilePicked(e: Event) {
    const input = e.currentTarget as HTMLInputElement;
    addFiles(Array.from(input.files ?? []));
    input.value = ''; // so picking the same photo again still works
  }

  function adjustHeight() {
    if (textareaElement) {
      textareaElement.style.height = 'auto';
      textareaElement.style.height = `${Math.min(textareaElement.scrollHeight, 180)}px`;
    }
  }

  onMount(() => {
    adjustHeight();
  });

  function isMobile(): boolean {
    if (typeof window === 'undefined') return false;
    return (
      ('ontouchstart' in window) ||
      (navigator.maxTouchPoints > 0) ||
      window.matchMedia('(pointer: coarse)').matches ||
      window.innerWidth < 1024
    );
  }

  function handleKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' || e.keyCode === 13) {
      // On mobile or touch devices, let the keyboard insert a newline naturally
      if (isMobile()) {
        return;
      }

      // Desktop behavior: Enter submits, Shift+Enter creates a newline
      if (!e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    }
  }

  async function handleSubmit() {
    if (!canSend || terminal.isStreaming) return;
    const text = inputPrompt;
    const attachments = readyAttachments;
    pending.forEach((p) => URL.revokeObjectURL(p.preview));
    pending = [];
    attachNotice = '';
    inputPrompt = '';
    await tick();
    adjustHeight();
    terminal.sendMessage(text, attachments);
  }

  function handleAbort() {
    terminal.abortGeneration();
  }
</script>

<div class="p-3.5 bg-slate-950/90 border-t border-slate-800/80 backdrop-blur-md shrink-0">
  <div class="max-w-4xl mx-auto flex flex-col gap-2">
    {#if pending.length || attachNotice}
      <div class="flex flex-wrap items-center gap-2 px-1">
        {#each pending as p (p.key)}
          <div class="relative w-16 h-16 rounded-lg overflow-hidden border {p.status === 'error' ? 'border-rose-500/60' : 'border-slate-700'} bg-slate-900" title={p.status === 'error' ? p.error : p.name}>
            <img src={p.preview} alt={p.name} class="w-full h-full object-cover {p.status === 'ready' ? '' : 'opacity-40'}" />
            {#if p.status === 'uploading'}
              <RefreshCw class="absolute inset-0 m-auto w-5 h-5 text-sky-300 animate-spin" />
            {:else if p.status === 'error'}
              <span class="absolute inset-0 flex items-center justify-center text-[10px] font-mono text-rose-300 bg-slate-950/60">failed</span>
            {/if}
            <button type="button" onclick={() => removePending(p.key)} class="absolute top-0.5 right-0.5 p-0.5 rounded-full bg-slate-950/80 text-slate-300 hover:text-white" aria-label="Remove image">
              <X class="w-3 h-3" />
            </button>
          </div>
        {/each}
        {#if pending.some((p) => p.status === 'error')}
          <span class="text-[11px] font-mono text-rose-400 max-w-xs">{pending.find((p) => p.status === 'error')?.error} Remove it and try again.</span>
        {/if}
        {#if attachNotice}<span class="text-[11px] font-mono text-amber-400">{attachNotice}</span>{/if}
      </div>
    {/if}

    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
      ondragover={(e) => { e.preventDefault(); dragging = true; }}
      ondragleave={() => (dragging = false)}
      ondrop={handleDrop}
      class="relative flex items-end gap-2 bg-slate-900/90 border border-slate-800 rounded-xl px-3.5 py-2.5 shadow-inner focus-within:border-sky-500/60 focus-within:ring-1 focus-within:ring-sky-500/30 transition-all {dragging ? 'border-sky-400 ring-1 ring-sky-400/50' : ''}"
    >
      <div class="text-sky-400 pb-1.5 shrink-0 font-mono text-xs select-none">
        <span class="text-emerald-400 font-bold">root@lyra:~#</span>
      </div>

      <textarea
        bind:this={textareaElement}
        bind:value={inputPrompt}
        oninput={adjustHeight}
        onkeydown={handleKeyDown}
        onpaste={handlePaste}
        placeholder="Enter prompt... (paste or drop an image to show it)"
        enterkeyhint="newline"
        wrap="soft"
        style="min-height: 24px;"
        class="flex-1 bg-transparent text-slate-100 text-xs font-mono leading-relaxed resize-none focus:outline-none placeholder-slate-500 max-h-44 overflow-y-auto py-0.5"
      ></textarea>

      <input bind:this={fileInput} type="file" accept="image/*" multiple class="hidden" onchange={handleFilePicked} />

      <div class="flex items-center gap-1.5 pb-0.5 shrink-0">
        <button
          type="button"
          onclick={() => fileInput?.click()}
          class="p-1.5 rounded-lg text-slate-400 hover:text-sky-300 hover:bg-slate-800 transition-colors"
          title="Attach an image"
          aria-label="Attach an image"
        >
          <Paperclip class="w-4 h-4" />
        </button>
        {#if terminal.isStreaming}
          <button
            type="button"
            onclick={handleAbort}
            class="flex items-center gap-1 px-3 py-1.5 bg-rose-500/20 hover:bg-rose-500/30 text-rose-300 border border-rose-500/40 rounded-lg text-xs font-mono transition-colors"
            title="Abort stream generation (Esc)"
          >
            <Square class="w-3.5 h-3.5 fill-current" />
            <span class="hidden sm:inline">Abort</span>
          </button>
        {:else}
          <button
            type="button"
            onclick={handleSubmit}
            disabled={!canSend}
            class="flex items-center gap-1 px-3 py-1.5 transition-all shadow-sm rounded-lg text-xs font-medium font-mono disabled:opacity-30 disabled:bg-sky-600/30 bg-sky-600 hover:bg-sky-500 text-white"
            title="Submit prompt"
          >
            <Send class="w-3.5 h-3.5" />
            <span class="hidden sm:inline">Send</span>
          </button>
        {/if}
      </div>
    </div>

    <div class="flex items-center justify-between text-[10px] font-mono text-slate-500 px-1">
      <div class="flex items-center gap-3">
        <span class="hidden sm:inline"><kbd class="px-1 py-0.5 bg-slate-900 border border-slate-800 rounded text-slate-400">↵</kbd> submit</span>
        <span class="hidden sm:inline"><kbd class="px-1 py-0.5 bg-slate-900 border border-slate-800 rounded text-slate-400">⇧↵</kbd> newline</span>
        <span class="sm:hidden">Tap Send to submit</span>
        <span class="hidden sm:inline">Gateway :8401</span>
      </div>
      <div class="flex items-center gap-2">
        <span title="Conversation size (corrected by the model server's own count) / how much is kept. The rest of the window is left for her next thinking and reply.">Context: ~{terminal.totalTokenCount} / {terminal.contextLimit} tok</span>
        {#if terminal.lastPromptTokens}
          <span class="hidden sm:inline" title="Exact size of the last request, as the model server counted it">(last sent: {terminal.lastPromptTokens})</span>
        {/if}
        <span>•</span>
        <span class={terminal.isStreaming ? 'text-amber-400 animate-pulse' : 'text-emerald-400'}>
          {terminal.isStreaming ? 'STREAMING' : 'READY'}
        </span>
      </div>
    </div>
  </div>
</div>
