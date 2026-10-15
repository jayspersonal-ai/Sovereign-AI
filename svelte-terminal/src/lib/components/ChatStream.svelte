<script lang="ts">
  import type { TerminalState } from '../terminal.svelte';
  import Avatar from './Avatar.svelte';
  import type { Message } from '../types';
  import { copyToClipboard } from '../clipboard';
  import { splitAttachments, workspaceImageUrl } from '../attachments';
  import { Bot, User, Terminal, Sparkles, Copy, Check, Code, Pencil, Trash2, RefreshCw } from 'lucide-svelte';

  interface Props {
    terminal: TerminalState;
  }

  let { terminal }: Props = $props();

  let streamContainer: HTMLDivElement | null = $state(null);
  let contentWrapper: HTMLDivElement | null = $state(null);
  let isAutoScrollLocked = $state(true);
  let copiedId = $state<string | null>(null);
  let copiedCodeKey = $state<string | null>(null);

  // Editing and deleting messages
  let editingId = $state<string | null>(null);
  let draft = $state('');
  let busyId = $state<string | null>(null);
  let notice = $state<{ id: string; ok: boolean; text: string } | null>(null);

  function showNotice(id: string, ok: boolean, text: string) {
    notice = { id, ok, text };
    setTimeout(() => {
      if (notice?.text === text) notice = null;
    }, 6000);
  }
  function startEdit(msg: Message) {
    if (terminal.isStreaming) return;
    editingId = msg.id;
    draft = msg.content;
    notice = null;
  }
  function cancelEdit() {
    editingId = null;
    draft = '';
  }
  async function saveEdit(msg: Message) {
    busyId = msg.id;
    const result = await terminal.editMessage(msg.id, draft);
    busyId = null;
    if (result.ok) cancelEdit();
    showNotice(msg.id, result.ok, result.message);
  }
  async function removeMessage(msg: Message) {
    if (!confirm("Delete this message? It is removed from the database as well, and this can't be undone.")) return;
    busyId = msg.id;
    const result = await terminal.deleteMessage(msg.id);
    busyId = null;
    if (!result.ok) showNotice(msg.id, false, result.message);
  }
  async function regenerate(msg: Message) {
    busyId = msg.id;
    const result = await terminal.regenerateLast();
    busyId = null;
    if (!result.ok) showNotice(msg.id, false, result.message);
    // A success worth mentioning (the old reply couldn't be removed from the database) belongs on the new reply.
    else if (result.message !== 'Regenerated.') showNotice(terminal.messages[terminal.messages.length - 1]?.id ?? msg.id, false, result.message);
  }
  // A different session: close any open editor
  $effect(() => {
    void terminal.activeSessionId;
    editingId = null;
    notice = null;
  });

  function handleScroll() {
    if (!streamContainer) return;
    const { scrollTop, scrollHeight, clientHeight } = streamContainer;
    isAutoScrollLocked = scrollHeight - (scrollTop + clientHeight) < 80;
  }

  function scrollToBottom() {
    if (streamContainer && isAutoScrollLocked) {
      streamContainer.scrollTop = streamContainer.scrollHeight;
    }
  }

  $effect(() => {
    if (!contentWrapper) return;
    const observer = new ResizeObserver(() => {
      if (isAutoScrollLocked) {
        scrollToBottom();
      }
    });
    observer.observe(contentWrapper);
    return () => {
      observer.disconnect();
    };
  });

  // Copying works on plain http pages too (see clipboard.ts). If it still fails, say so instead of doing nothing.
  const COPY_FAILED = "Couldn't copy. Select the text and copy it by hand.";

  async function copyContent(id: string, text: string) {
    if (!(await copyToClipboard(text))) {
      showNotice(id, false, COPY_FAILED);
      return;
    }
    copiedId = id;
    setTimeout(() => {
      if (copiedId === id) copiedId = null;
    }, 2000);
  }

  async function copyCode(msgId: string, key: string, code: string) {
    if (!(await copyToClipboard(code))) {
      showNotice(msgId, false, COPY_FAILED);
      return;
    }
    copiedCodeKey = key;
    setTimeout(() => {
      if (copiedCodeKey === key) copiedCodeKey = null;
    }, 2000);
  }

  interface ContentBlock {
    type: 'code' | 'text';
    content: string;
    lang?: string;
  }

  function parseMarkdownBlocks(text: string): ContentBlock[] {
    if (!text) return [];

    const blocks: ContentBlock[] = [];
    const fenceRegex = /```([a-zA-Z0-9_\-+]*)\n([\s\S]*?)```/g;
    let lastIndex = 0;
    let match: RegExpExecArray | null;

    while ((match = fenceRegex.exec(text)) !== null) {
      if (match.index > lastIndex) {
        const textBefore = text.slice(lastIndex, match.index).replace(/^\n/, '');
        if (textBefore) {
          blocks.push({
            type: 'text',
            content: textBefore
          });
        }
      }

      blocks.push({
        type: 'code',
        lang: match[1] || 'bash',
        content: match[2].trimEnd()
      });

      lastIndex = match.index + match[0].length;
    }

    if (lastIndex < text.length) {
      const remaining = text.slice(lastIndex);
      const openFenceIndex = remaining.indexOf('```');

      if (openFenceIndex !== -1) {
        if (openFenceIndex > 0) {
          const textBefore = remaining.slice(0, openFenceIndex).replace(/^\n/, '');
          if (textBefore) {
            blocks.push({
              type: 'text',
              content: textBefore
            });
          }
        }
        const afterFence = remaining.slice(openFenceIndex + 3);
        const firstNewline = afterFence.indexOf('\n');
        if (firstNewline !== -1) {
          blocks.push({
            type: 'code',
            lang: afterFence.slice(0, firstNewline).trim() || 'text',
            content: afterFence.slice(firstNewline + 1)
          });
        } else {
          blocks.push({
            type: 'code',
            lang: afterFence.trim() || 'text',
            content: ''
          });
        }
      } else {
        const trailingText = remaining.replace(/^\n/, '');
        if (trailingText) {
          blocks.push({
            type: 'text',
            content: trailingText
          });
        }
      }
    }

    return blocks;
  }
</script>

<div
  bind:this={streamContainer}
  onscroll={handleScroll}
  class="flex-1 overflow-y-auto p-4 sm:p-6 no-scrollbar font-mono text-xs selection:bg-sky-500/30"
>
  <div bind:this={contentWrapper} class="space-y-4">
    {#if terminal.messages.length === 0}
      <div class="h-64 flex flex-col items-center justify-center text-slate-500 gap-2 select-none">
        <Terminal class="w-8 h-8 stroke-[1.5] text-slate-600"/>
        <p class="text-xs">Terminal buffer empty. Send a prompt to start stream.</p>
      </div>
    {/if}

    {#snippet actions(msg: Message)}
      {#if !msg.isStreaming}
        {#if msg.role === 'assistant' && terminal.canRegenerate(msg)}
          <button onclick={() => regenerate(msg)} disabled={terminal.isStreaming || busyId === msg.id} class="p-1.5 text-slate-500 hover:text-emerald-400 transition-colors disabled:opacity-40" title="Regenerate this reply"><RefreshCw class="w-3 h-3" /></button>
        {/if}
        <button onclick={() => startEdit(msg)} disabled={terminal.isStreaming || busyId === msg.id} class="p-1.5 text-slate-500 hover:text-sky-300 transition-colors disabled:opacity-40" title="Edit message"><Pencil class="w-3 h-3" /></button>
        <button onclick={() => removeMessage(msg)} disabled={terminal.isStreaming || busyId === msg.id} class="p-1.5 text-slate-500 hover:text-rose-400 transition-colors disabled:opacity-40" title="Delete message"><Trash2 class="w-3 h-3" /></button>
      {/if}
    {/snippet}

    {#snippet messageEditor(msg: Message)}
      <div class="space-y-2">
        <textarea
          bind:value={draft}
          rows="8"
          class="w-full bg-slate-900 border border-slate-700 rounded-lg p-3 text-xs text-slate-100 font-sans leading-relaxed outline-none resize-y focus:border-sky-500/60"
        ></textarea>
        <div class="flex flex-wrap items-center gap-2">
          <button onclick={() => saveEdit(msg)} disabled={busyId === msg.id} class="px-3 py-2 rounded-lg bg-indigo-600 border border-indigo-500 text-white text-[11px] font-mono hover:bg-indigo-500 disabled:opacity-60">{busyId === msg.id ? 'Saving\u2026' : 'Save'}</button>
          <button onclick={cancelEdit} disabled={busyId === msg.id} class="px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-slate-300 text-[11px] font-mono hover:bg-slate-800 disabled:opacity-60">Cancel</button>
          <span class="text-[10px] text-slate-600 font-mono">~{Math.max(1, Math.round(draft.length / 3.8))} tok</span>
        </div>
      </div>
    {/snippet}

    {#snippet noticeLine(msg: Message)}
      {#if notice?.id === msg.id}
        <p class="mt-2 text-[11px] font-sans leading-relaxed {notice.ok ? 'text-emerald-400' : 'text-rose-400'}">{notice.text}</p>
      {/if}
    {/snippet}

    {#each terminal.messages as msg (msg.id)}
      {#if msg.role === 'system'}
        <!-- System / Checkpoint Message -->
        <div class="p-3 rounded-lg bg-slate-900/50 border border-slate-800/80 text-[11px] text-slate-400 flex items-start gap-2.5">
          <Terminal class="w-3.5 h-3.5 text-sky-400 shrink-0 mt-0.5"/>
          <div class="flex-1 whitespace-pre-wrap leading-relaxed">{msg.content}</div>
          <span class="text-[10px] text-slate-500 shrink-0 font-mono">
            {new Date(msg.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
          </span>
        </div>

      {:else if msg.role === 'user'}
        <!-- User Prompt Entry -->
        <div class="p-3.5 rounded-xl bg-slate-900/90 border border-slate-800 text-slate-200 ml-6 sm:ml-16 shadow-xs">
          <div class="flex items-center justify-between pb-2 mb-2 border-b border-slate-800/70 text-[11px] text-slate-400 font-mono">
            <div class="flex items-center gap-1.5 text-sky-400 font-semibold">
              {#if terminal.userAvatar}
                <Avatar src={terminal.userAvatar} name={terminal.userName} size="md" />
              {:else}
                <User class="w-3.5 h-3.5"/>
              {/if}
              <span>{terminal.userName}</span>
            </div>
            <div class="flex items-center gap-2">
              {@render actions(msg)}
              <span>~{msg.tokens} tok</span>
              <span>•</span>
              <span>{new Date(msg.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
            </div>
          </div>
          {#if editingId === msg.id}
            {@render messageEditor(msg)}
          {:else}
            {@const shown = splitAttachments(msg.content)}
            {#if shown.paths.length}
              <div class="flex flex-wrap gap-2 {shown.text ? 'mb-2.5' : ''}">
                {#each shown.paths as p (p)}
                  <a href={workspaceImageUrl(p)} target="_blank" rel="noopener" title={p}>
                    <img src={workspaceImageUrl(p)} alt={p.split('/').pop()} loading="lazy" class="max-h-48 max-w-full rounded-lg border border-slate-700 bg-slate-900 object-contain text-[10px] text-slate-500" />
                  </a>
                {/each}
              </div>
            {/if}
            {#if shown.text}
              <div class="text-xs leading-relaxed whitespace-pre-wrap font-sans text-slate-100">{shown.text}</div>
            {/if}
          {/if}
          {@render noticeLine(msg)}
        </div>

      {:else}
        <!-- Assistant Response Entry -->
        <div class="p-3.5 rounded-xl bg-slate-950 border border-slate-800/90 text-slate-200 mr-4 sm:mr-12 shadow-md">
          <div class="flex items-center justify-between pb-2 mb-2.5 border-b border-slate-800/70 text-[11px] text-slate-400 font-mono">
            <div class="flex items-center gap-1.5 text-emerald-400 font-semibold">
              {#if terminal.characterAvatar}
                <Avatar src={terminal.characterAvatar} name={terminal.characterName} size="md" />
              {:else}
                <Bot class="w-3.5 h-3.5"/>
              {/if}
              <span>{terminal.characterName} ({msg.model ?? terminal.backendStatus.model})</span>
              {#if msg.isStreaming}
                <span class="inline-block w-1.5 h-1.5 rounded-full bg-amber-400 animate-ping ml-1"></span>
              {/if}
            </div>
            <div class="flex items-center gap-2">
              {@render actions(msg)}
              <button
                onclick={() => copyContent(msg.id, msg.content)}
                class="p-1 hover:text-slate-200 transition-colors"
                title="Copy entire message"
              >
                {#if copiedId === msg.id}
                  <Check class="w-3 h-3 text-emerald-400"/>
                {:else}
                  <Copy class="w-3 h-3 text-slate-500"/>
                {/if}
              </button>
              <span>~{msg.tokens} tok</span>
              <span>•</span>
              <span>{new Date(msg.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
            </div>
          </div>

          <!-- Thinking Accordion -->
          {#if msg.thinking}
            <details class="mb-3 group rounded-lg bg-slate-900/60 border border-slate-800/80 overflow-hidden text-[11px]" open={msg.isStreaming}>
              <summary class="flex items-center justify-between px-3 py-1.5 cursor-pointer bg-slate-900/80 hover:bg-slate-850 select-none text-amber-400 font-semibold transition-colors">
                <div class="flex items-center gap-1.5">
                  <Sparkles class="w-3.5 h-3.5 text-amber-400"/>
                  <span>Cognitive Reasoning ({msg.thinking.length} chars)</span>
                </div>
                <span class="text-[10px] text-slate-500 font-mono">
                  {msg.isStreaming ? 'THINKING...' : 'RESOLVED'}
                </span>
              </summary>
              <pre style="font-size: var(--think-size, 0.625rem)" class="p-3 font-mono leading-relaxed text-slate-400 whitespace-pre-wrap border-t border-slate-800/80 max-h-48 overflow-y-auto">{msg.thinking}</pre>
            </details>
          {/if}

          {#if editingId === msg.id}
            {@render messageEditor(msg)}
          {:else}
          <!-- Structured Message Body with Formatted Code Fences -->
          <div class="space-y-3 font-sans text-xs leading-relaxed text-slate-200">
            {#each parseMarkdownBlocks(msg.content) as block, idx (`${msg.id}-block-${idx}`)}
              {#if block.type === 'code'}
                <!-- Formatted Code Block Container -->
                <div class="rounded-xl overflow-hidden border border-slate-800 bg-slate-900/90 font-mono text-[11px] shadow-sm my-2">
                  <div class="flex items-center justify-between px-3 py-1.5 bg-slate-850 border-b border-slate-800 text-slate-400">
                    <div class="flex items-center gap-1.5 text-sky-400">
                      <Code class="w-3 h-3"/>
                      <span class="uppercase font-semibold tracking-wider text-[10px]">{block.lang || 'code'}</span>
                    </div>
                    <button
                      onclick={() => copyCode(msg.id, `${msg.id}-${idx}`, block.content)}
                      class="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] hover:text-slate-200 hover:bg-slate-800 transition-colors"
                      title="Copy code"
                    >
                      {#if copiedCodeKey === `${msg.id}-${idx}`}
                        <Check class="w-3 h-3 text-emerald-400"/>
                        <span class="text-emerald-400">Copied</span>
                      {:else}
                        <Copy class="w-3 h-3"/>
                        <span>Copy</span>
                      {/if}
                    </button>
                  </div>
                  <pre class="p-3 overflow-x-auto text-sky-200 leading-relaxed no-scrollbar whitespace-pre">{block.content}</pre>
                </div>
              {:else}
                <!-- Text Block -->
                <div class="whitespace-pre-wrap font-sans leading-relaxed text-slate-200">
                  {block.content}
                </div>
              {/if}
            {/each}

            {#if msg.isStreaming && !msg.content}
              <span class="inline-block w-2 h-3.5 bg-sky-400 animate-pulse ml-0.5"></span>
            {/if}
          </div>
          {/if}
          {@render noticeLine(msg)}
        </div>
      {/if}
    {/each}
  </div>
</div>
