<script lang="ts">
  import { onMount } from 'svelte';
  import { TerminalState } from './lib/terminal.svelte';
  import ChatStream from './lib/components/ChatStream.svelte';
  import CommandBar from './lib/components/CommandBar.svelte';
  import WorkspacePanel from './lib/components/WorkspacePanel.svelte';
  import LorebookPanel from './lib/components/LorebookPanel.svelte';
  import SaveBar from './lib/components/SaveBar.svelte';
  import DataPanel from './lib/components/DataPanel.svelte';
  import Avatar from './lib/components/Avatar.svelte';

  import {
    Terminal, Plus, Trash2, Bookmark, Layers,
    ChevronLeft, ChevronRight, Radio, Menu, Settings, BookOpen, Pencil, Upload
  } from 'lucide-svelte';

  const terminal = new TerminalState();

  // Shared class strings for the card editor (keeps the markup below readable)
  const lbl = 'text-[10px] font-mono text-slate-500 uppercase tracking-wider';
  const inp = 'w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-100 font-mono outline-none focus:border-indigo-500';
  const ta = 'w-full bg-slate-900 border border-slate-800 rounded-lg p-3 text-xs text-slate-200 font-sans leading-relaxed outline-none resize-none focus:border-indigo-500';
  const sec = 'rounded-lg border border-slate-800 bg-slate-900/30';
  const sum = 'cursor-pointer select-none px-3 py-2.5 text-xs font-mono text-slate-300 flex items-center justify-between gap-2';
  const sent = 'text-[9px] font-mono text-emerald-400/80 shrink-0';
  const notSent = 'text-[9px] font-mono text-slate-600 shrink-0';
  const tok = (text?: string) => Math.round((text ?? '').length / 3.8);

  // Profile picture chosen from the device (gallery or camera)
  let avatarError = $state('');
  async function pickAvatar(e: Event & { currentTarget: HTMLInputElement }, doc: NonNullable<typeof terminal.activeCharacterDoc>) {
    const input = e.currentTarget;
    const file = input.files?.[0];
    input.value = ''; // lets you pick the same file again
    if (!file) return;
    avatarError = '';
    try {
      await terminal.setAvatarFromFile(doc, file);
    } catch (err) {
      avatarError = err instanceof Error ? err.message : 'That image could not be used.';
    }
  }

  // Your own picture (gallery or camera)
  let userAvatarError = $state('');
  async function pickUserAvatar(e: Event & { currentTarget: HTMLInputElement }) {
    const input = e.currentTarget;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    userAvatarError = '';
    try {
      await terminal.setUserAvatarFromFile(file);
    } catch (err) {
      userAvatarError = err instanceof Error ? err.message : 'That image could not be used.';
    }
  }

  // Session sidebar helpers
  function renameSession(sess: { id: string; title: string }) {
    const next = prompt('Rename session (leave empty to reset to the original name)', sess.title);
    if (next !== null) terminal.renameSession(sess.id, next);
  }
  function removeSession(sess: { id: string; title: string }) {
    if (confirm(`Delete "${sess.title}" and all of its messages? This can't be undone.`)) void terminal.deleteSession(sess.id);
  }
  const sessionSubtitle = (s: { messageCount: number; updatedAt: Date }) =>
    s.messageCount > 0 ? `${s.messageCount} turns` : new Date(s.updatedAt).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });

  function confirmDeleteCharacter() {
    if (confirm(`Delete character "${terminal.characterName}"? This removes its card file.`)) terminal.deleteActiveCharacter();
  }

  let isRailExpanded = $state(true);
  let isMobileSidebarOpen = $state(false);
  let configTab = $state<'character' | 'system' | 'data'>('character');

  onMount(async () => {
    await terminal.loadData();
    terminal.hydrateSessionFromSQL(terminal.activeSessionId);
  });

  function handleWindowKeyDown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      if (terminal.isStreaming) {
        e.preventDefault();
        terminal.abortGeneration();
      } else if (terminal.isWorkspaceOpen) {
        terminal.toggleWorkspace(false);
      } else if (terminal.isConfigOpen) {
        terminal.toggleConfig(false);
      } else if (terminal.isLorebookViewOpen) {
        terminal.toggleLorebookView(false);
      } else if (isMobileSidebarOpen) {
        isMobileSidebarOpen = false;
      }
    }
  }
</script>

<svelte:window onkeydown={handleWindowKeyDown} />

<div class="flex h-[100dvh] w-screen bg-slate-950 text-slate-100 font-sans antialiased overflow-hidden select-text relative">

  {#if isMobileSidebarOpen}
    <div class="fixed inset-0 bg-black/60 z-40 md:hidden backdrop-blur-sm" onclick={() => (isMobileSidebarOpen = false)} aria-hidden="true"></div>
  {/if}

  <!-- 1. LEFT NAVIGATION RAIL -->
  <aside class="fixed inset-y-0 left-0 bg-slate-950 border-r border-slate-800/80 flex flex-col justify-between shrink-0 transition-all duration-300 z-50 overflow-hidden md:relative md:translate-x-0 {isRailExpanded ? 'w-60' : 'w-16'} {isMobileSidebarOpen ? 'translate-x-0' : '-translate-x-full'}">
    <div class="flex flex-col flex-1 min-h-0 overflow-hidden">
      <div class="flex items-center justify-between p-3.5 border-b border-slate-800/80 bg-slate-900/40 shrink-0">
        <div class="flex items-center gap-2.5 overflow-hidden">
          <div class="w-8 h-8 rounded-lg bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400 shrink-0 shadow-xs">
            <Terminal class="w-4 h-4" />
          </div>
          {#if isRailExpanded}
            <div class="flex flex-col truncate">
              <span class="text-xs font-semibold font-mono tracking-tight text-slate-100">SOVEREIGN TERMINAL</span>
              <span class="text-[10px] font-mono text-emerald-400">Svelte 5 Runes Thin Client</span>
            </div>
          {/if}
        </div>
        <button onclick={() => (isRailExpanded = !isRailExpanded)} class="hidden md:block p-1 rounded-md text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition-colors shrink-0" title={isRailExpanded ? 'Collapse Rail' : 'Expand Rail'}>
          {#if isRailExpanded}<ChevronLeft class="w-4 h-4" />{:else}<ChevronRight class="w-4 h-4" />{/if}
        </button>
        <button onclick={() => (isMobileSidebarOpen = false)} class="md:hidden p-1 rounded-md text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition-colors shrink-0" title="Close Menu">
          <ChevronLeft class="w-4 h-4" />
        </button>
      </div>

      <div class="p-2 border-b border-slate-800/60 space-y-1 shrink-0">
        <button onclick={() => { terminal.createNewSession(); isMobileSidebarOpen = false; }} class="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-sky-600/20 hover:bg-sky-600/30 border border-sky-500/40 text-sky-300 text-xs font-mono transition-all">
          <Plus class="w-3.5 h-3.5 shrink-0" />
          {#if isRailExpanded}<span class="truncate">New Session</span>{/if}
        </button>
        <div class="grid grid-cols-2 gap-1 pt-1">
          <button onclick={() => { terminal.clearScreen(); isMobileSidebarOpen = false; }} class="flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-md bg-slate-900 hover:bg-slate-850 border border-slate-800 text-[11px] font-mono text-slate-400 hover:text-slate-200 transition-colors">
            <Trash2 class="w-3 h-3 text-rose-400 shrink-0" />
            {#if isRailExpanded}<span>Clear</span>{/if}
          </button>
          <button onclick={() => { terminal.addCheckpoint(); isMobileSidebarOpen = false; }} class="flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-md bg-slate-900 hover:bg-slate-850 border border-slate-800 text-[11px] font-mono text-slate-400 hover:text-slate-200 transition-colors">
            <Bookmark class="w-3 h-3 text-amber-400 shrink-0" />
            {#if isRailExpanded}<span>Mark</span>{/if}
          </button>
        </div>
      </div>

      {#if isRailExpanded}
        <div class="px-3 pt-3 pb-1 text-[10px] font-mono font-semibold uppercase tracking-wider text-slate-500 shrink-0">Chat Sessions</div>
      {/if}

      <div class="flex-1 min-h-0 overflow-y-auto px-2 py-1 space-y-1 no-scrollbar">
        {#each terminal.sessions as sess (sess.id)}
          <div class="w-full flex items-center rounded-lg text-xs font-mono transition-all border {terminal.activeSessionId === sess.id ? 'bg-slate-800/90 text-sky-300 border-sky-500/40 shadow-xs' : 'text-slate-400 border-transparent hover:bg-slate-900 hover:text-slate-200'}">
            <button onclick={() => { terminal.selectSession(sess.id); isMobileSidebarOpen = false; }} class="flex-1 min-w-0 flex items-center gap-2 px-2.5 py-2 text-left">
              <Radio class="w-3 h-3 shrink-0 {terminal.activeSessionId === sess.id ? 'text-sky-400' : 'text-slate-600'}" />
              {#if isRailExpanded}
                <div class="truncate flex-1">
                  <div class="truncate font-medium text-[11px]">{sess.title}</div>
                  <div class="text-[9px] text-slate-500">{sessionSubtitle(sess)}</div>
                </div>
              {/if}
            </button>
            {#if isRailExpanded}
              <button onclick={() => renameSession(sess)} class="p-2.5 shrink-0 text-slate-500 hover:text-sky-300" title="Rename session"><Pencil class="w-3.5 h-3.5" /></button>
              <button onclick={() => removeSession(sess)} class="p-2.5 pr-3 shrink-0 text-slate-500 hover:text-rose-400" title="Delete session"><Trash2 class="w-3.5 h-3.5" /></button>
            {/if}
          </div>
        {/each}
      </div>
    </div>

    <div class="p-3 border-t border-slate-800/80 bg-slate-900/30 shrink-0">
      <div class="flex items-center gap-2">
        <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
        {#if isRailExpanded}
          <div class="flex flex-col truncate font-mono text-[10px]">
            <span class="text-slate-200 font-semibold truncate">{terminal.backendStatus.model}</span>
            <span class="text-slate-500" title="Conversation size / how much is kept">Context: {(terminal.totalTokenCount / 1000).toFixed(1)}K / {(terminal.contextLimit / 1000).toFixed(1)}K</span>
          </div>
        {/if}
      </div>
    </div>
  </aside>

  <!-- 2. CENTER CANVAS -->
  <main class="flex-1 flex flex-col h-full min-w-0 overflow-hidden bg-slate-950 relative">
    <header class="flex items-center justify-between px-4 sm:px-5 py-2.5 border-b border-slate-800/80 bg-slate-900/40 shrink-0">
      <div class="flex items-center gap-2 sm:gap-3 text-xs font-mono truncate">
        <button class="md:hidden p-1 -ml-1 text-slate-400 hover:text-slate-200 transition-colors" onclick={() => (isMobileSidebarOpen = true)}>
          <Menu class="w-4 h-4" />
        </button>
        <span class="text-slate-400 hidden sm:inline">Node:</span>
        <select
          value={terminal.backendStatus.model}
          onchange={(e) => terminal.selectModel(e.currentTarget.value)}
          disabled={terminal.isStreaming || terminal.modelChoices.length < 2}
          title={terminal.modelsError ? `Couldn't load the model list: ${terminal.modelsError}` : 'Switch model'}
          class="px-2 py-0.5 rounded bg-slate-900 text-sky-300 border border-slate-800 text-[11px] max-w-[12rem] truncate outline-none focus:border-sky-500/60 disabled:opacity-100"
        >
          {#each terminal.modelChoices as m (m)}
            <option value={m}>{m}</option>
          {/each}
        </select>
        <span class="text-slate-600">|</span>
        <span class="text-slate-400 hidden sm:inline">Split:</span>
        <span class="text-emerald-400 font-semibold text-[11px]">[1.0, 1.0]</span>
      </div>

      <div class="flex items-center gap-2">
        <!-- LOREBOOK EXPLORER ACTION BUTTON -->
        <button onclick={() => terminal.toggleLorebookView()} class="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-mono font-medium transition-all border {terminal.isLorebookViewOpen ? 'bg-amber-600 text-white border-amber-500 shadow-sm' : 'bg-slate-900 hover:bg-slate-850 text-slate-300 border-slate-800'}" title="Open Lorebook Workspace Explorer">
          <BookOpen class="w-3.5 h-3.5 {terminal.isLorebookViewOpen ? 'text-white' : 'text-amber-400'}" />
          <span class="hidden sm:inline">Lorebook</span>
          {#if terminal.activeSession?.lorebookEnabled}
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
          {/if}
        </button>

        <button onclick={() => terminal.toggleConfig()} class="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-mono font-medium transition-all border {terminal.isConfigOpen ? 'bg-indigo-600 text-white border-indigo-500 shadow-sm' : 'bg-slate-900 hover:bg-slate-850 text-slate-300 border-slate-800'}">
          <Settings class="w-3.5 h-3.5 {terminal.isConfigOpen ? 'text-white' : 'text-indigo-400'}" />
          <span class="hidden sm:inline">Persona</span>
        </button>

        <button onclick={() => terminal.toggleWorkspace()} class="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-mono font-medium transition-all border {terminal.isWorkspaceOpen ? 'bg-sky-600 text-white border-sky-500 shadow-sm' : 'bg-slate-900 hover:bg-slate-850 text-slate-300 border-slate-800'}">
          <Layers class="w-3.5 h-3.5 text-sky-400" />
          <span class="hidden sm:inline">Inspector</span>
          {#if terminal.workspaceItems.length > 0}
            <span class="px-1 py-0.2 rounded bg-sky-500/20 text-sky-300 text-[10px]">{terminal.workspaceItems.length}</span>
          {/if}
        </button>
      </div>
    </header>

    <!-- MAIN TEMPLATE VIEWPORT SPLIT CANVAS -->
    {#if terminal.isLorebookViewOpen}
      <div class="flex-1 flex overflow-hidden bg-slate-950">
        <LorebookPanel {terminal} />
      </div>
    {:else}
      <ChatStream {terminal} />
    {/if}

    <CommandBar {terminal} />
  </main>

  {#if terminal.isWorkspaceOpen}
    <div class="fixed inset-0 z-50 lg:static flex justify-end bg-black/60 lg:bg-transparent backdrop-blur-xs lg:backdrop-blur-none">
      <WorkspacePanel {terminal} />
    </div>
  {/if}

  {#if terminal.isConfigOpen}
    <div class="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4 sm:p-6">
      <div class="bg-slate-950 border border-slate-800 rounded-xl w-full max-w-2xl shadow-2xl flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        <div class="flex items-center justify-between px-5 py-4 border-b border-slate-800 bg-slate-900/50">
          <div class="flex items-center gap-2 text-indigo-400">
            <Settings class="w-5 h-5" />
            <h2 class="text-sm font-semibold font-mono text-slate-200">Persona & System Rules</h2>
          </div>
          <button onclick={() => terminal.toggleConfig(false)} class="text-slate-500 hover:text-slate-300">
            <kbd class="px-1.5 py-0.5 bg-slate-900 border border-slate-700 rounded text-[10px] font-mono">ESC</kbd>
          </button>
        </div>

        <div class="flex items-center gap-5 px-5 pt-3 border-b border-slate-800 bg-slate-900/50 text-xs font-mono font-medium overflow-x-auto whitespace-nowrap">
          <button onclick={() => configTab = 'character'} class="pb-3 border-b-2 transition-colors {configTab === 'character' ? 'border-indigo-500 text-indigo-400' : 'border-transparent text-slate-500 hover:text-slate-300'}">Characters</button>
          <button onclick={() => configTab = 'system'} class="pb-3 border-b-2 transition-colors {configTab === 'system' ? 'border-indigo-500 text-indigo-400' : 'border-transparent text-slate-500 hover:text-slate-300'}">System Prompt</button>
          <button onclick={() => configTab = 'data'} class="pb-3 border-b-2 transition-colors {configTab === 'data' ? 'border-indigo-500 text-indigo-400' : 'border-transparent text-slate-500 hover:text-slate-300'}">Backup & Share</button>
        </div>

        <div class="p-5 overflow-y-auto max-h-[65vh] min-h-[350px] flex flex-col bg-slate-950">
          {#if configTab === 'character'}
            {#if terminal.activeCharacterDoc}
              {@const card = terminal.activeCharacterDoc}
              <div class="flex items-center gap-3 mb-5">
                <Avatar src={terminal.avatarOf(card)} name={card.data.data.name || '?'} size="md" />
                <select bind:value={terminal.activeCharacterId} class="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 font-mono outline-none focus:border-indigo-500">
                  {#each terminal.characterDocs as char (char.id)}
                    <option value={char.id}>{char.data.data.name}</option>
                  {/each}
                </select>
                <button onclick={() => terminal.createNewCharacter()} class="px-3 py-2 rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 text-xs font-mono" title="New character"><Plus class="w-4 h-4" /></button>
                <button onclick={confirmDeleteCharacter} disabled={terminal.characterDocs.length <= 1} class="px-3 py-2 rounded-lg bg-rose-500/10 text-rose-400 border border-rose-500/30 disabled:opacity-50 text-xs font-mono" title="Delete character"><Trash2 class="w-4 h-4" /></button>
              </div>

              <div class="space-y-4">
                <div class="flex items-center gap-4">
                  <Avatar src={terminal.avatarOf(card)} name={card.data.data.name || '?'} size="lg" />
                  <div class="space-y-2 min-w-0">
                    <div class="flex flex-wrap gap-2">
                      <label class="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-slate-700 bg-slate-900 text-slate-300 text-xs font-mono cursor-pointer hover:bg-slate-800">
                        <Upload class="w-4 h-4" /><span>Choose picture</span>
                        <input type="file" accept="image/*" class="hidden" onchange={(e) => pickAvatar(e, card)} />
                      </label>
                      {#if terminal.avatarOf(card)}
                        <button onclick={() => terminal.setAvatar(card, null)} class="px-3 py-2 rounded-lg border border-slate-800 text-slate-400 text-xs font-mono hover:text-slate-200">Remove</button>
                      {/if}
                    </div>
                    <p class="text-[10px] text-slate-600 leading-relaxed">Saved inside the card with the Save button. Tip: open an image in the workspace panel and tap "Use as picture".</p>
                    {#if avatarError}<p class="text-[11px] text-rose-400">{avatarError}</p>{/if}
                  </div>
                </div>

                <div class="space-y-1.5">
                  <div class="flex items-center justify-between">
                    <label class={lbl}>Display Name</label>
                    <span class={sent}>→ model</span>
                  </div>
                  <input type="text" bind:value={card.data.data.name} class={inp} />
                </div>

                <div class="space-y-1.5">
                  <div class="flex items-center justify-between">
                    <label class={lbl}>Description / Persona Rules</label>
                    <span class="text-[9px] font-mono text-slate-600"><span class="text-emerald-400/80">→ model</span> · ~{tok(card.data.data.description)} tok</span>
                  </div>
                  <textarea bind:value={card.data.data.description} class="{ta} h-40 sm:h-52" placeholder="Describe the character..."></textarea>
                </div>

                <details class={sec}>
                  <summary class={sum}><span>▸ Personality & Scenario</span><span class={sent}>→ model · ~{tok(card.data.data.personality) + tok(card.data.data.scenario)} tok</span></summary>
                  <div class="p-3 pt-1 space-y-3">
                    <div class="space-y-1.5">
                      <label class={lbl}>Personality</label>
                      <textarea bind:value={card.data.data.personality} class="{ta} h-24" placeholder="Traits, temperament, speech style..."></textarea>
                    </div>
                    <div class="space-y-1.5">
                      <label class={lbl}>Scenario</label>
                      <textarea bind:value={card.data.data.scenario} class="{ta} h-24" placeholder="Where and when the conversation takes place..."></textarea>
                    </div>
                  </div>
                </details>

                <details class={sec}>
                  <summary class={sum}><span>▸ Example Dialogue</span><span class={sent}>→ model · ~{tok(card.data.data.mes_example)} tok</span></summary>
                  <div class="p-3 pt-1 space-y-1.5">
                    <label class={lbl}>Use {'{{user}}'} and {'{{char}}'}; separate examples with &lt;START&gt;</label>
                    <textarea bind:value={card.data.data.mes_example} class="{ta} h-32" placeholder={'<START>\n{{user}}: Hello\n{{char}}: Hi there.'}></textarea>
                  </div>
                </details>

                <details class={sec}>
                  <summary class={sum}><span>▸ Character Instructions</span><span class={sent}>→ model · ~{tok(card.data.data.system_prompt) + tok(card.data.data.post_history_instructions)} tok</span></summary>
                  <div class="p-3 pt-1 space-y-3">
                    <div class="space-y-1.5">
                      <label class={lbl}>Character system prompt (added after the global prompt)</label>
                      <textarea bind:value={card.data.data.system_prompt} class="{ta} h-24"></textarea>
                    </div>
                    <div class="space-y-1.5">
                      <label class={lbl}>Standing instructions (added at the end of the prompt)</label>
                      <textarea bind:value={card.data.data.post_history_instructions} class="{ta} h-24"></textarea>
                    </div>
                  </div>
                </details>

                <details class={sec}>
                  <summary class={sum}><span>▸ Greetings</span><span class={notSent}>shown in chat</span></summary>
                  <div class="p-3 pt-1 space-y-3">
                    <div class="space-y-1.5">
                      <label class={lbl}>First message (used when a new session starts)</label>
                      <textarea bind:value={card.data.data.first_mes} class="{ta} h-24" placeholder="Leave empty for the default greeting"></textarea>
                    </div>
                    <div class="space-y-2">
                      <label class={lbl}>Alternate greetings (optional)</label>
                      {#each card.data.data.alternate_greetings ?? [] as _greeting, i}
                        <div class="flex items-start gap-2">
                          <textarea bind:value={card.data.data.alternate_greetings[i]} class="{ta} h-20 flex-1"></textarea>
                          <button onclick={() => card.data.data.alternate_greetings.splice(i, 1)} class="p-2.5 rounded-lg bg-rose-500/10 text-rose-400 border border-rose-500/30" title="Remove greeting"><Trash2 class="w-4 h-4" /></button>
                        </div>
                      {/each}
                      <button onclick={() => (card.data.data.alternate_greetings = [...(card.data.data.alternate_greetings ?? []), ''])} class="px-3 py-2 rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 text-xs font-mono">+ Add greeting</button>
                    </div>

                    {#if (card.data.data.alternate_greetings ?? []).length > 0}
                      <div class="space-y-1.5">
                        <label class={lbl}>New sessions start with</label>
                        <select value={terminal.greetingChoice(card)} onchange={(e) => terminal.setGreetingChoice(card, e.currentTarget.value)} class={inp}>
                          <option value="0">First message</option>
                          {#each card.data.data.alternate_greetings as alt, i}
                            <option value={String(i + 1)}>Alternate {i + 1}: {(alt || '(empty)').slice(0, 28)}</option>
                          {/each}
                          <option value="random">Random (first message or any alternate)</option>
                        </select>
                      </div>
                    {/if}
                  </div>
                </details>

                <details class={sec}>
                  <summary class={sum}><span>▸ Card Info</span><span class={notSent}>not sent to the model</span></summary>
                  <div class="p-3 pt-1 space-y-3">
                    <div class="grid grid-cols-2 gap-3">
                      <div class="space-y-1.5">
                        <label class={lbl}>Creator</label>
                        <input type="text" bind:value={card.data.data.creator} class={inp} />
                      </div>
                      <div class="space-y-1.5">
                        <label class={lbl}>Version</label>
                        <input type="text" bind:value={card.data.data.character_version} class={inp} />
                      </div>
                    </div>
                    <div class="space-y-1.5">
                      <label class={lbl}>Tags (comma separated)</label>
                      <input type="text" value={(card.data.data.tags ?? []).join(', ')} onchange={(e) => (card.data.data.tags = e.currentTarget.value.split(',').map((t) => t.trim()).filter(Boolean))} class={inp} />
                    </div>
                    <div class="space-y-1.5">
                      <label class={lbl}>Creator notes</label>
                      <textarea bind:value={card.data.data.creator_notes} class="{ta} h-24"></textarea>
                    </div>
                  </div>
                </details>
              </div>
            {:else}
              <p class="text-xs font-mono text-slate-500">Loading characters…</p>
            {/if}
          {:else if configTab === 'system'}
            <div class="space-y-1.5 mb-4">
              <label class={lbl}>Your name</label>
              <input type="text" bind:value={terminal.settingsDoc.data.userName} class={inp} placeholder="User" />
              <p class="text-[10px] text-slate-600 leading-relaxed">Fills {'{{user}}'} in character cards. Also saved as the name on your messages. Leave empty to use "User" in cards and the database default on messages.</p>
              <div class="flex items-center gap-4 pt-2">
                <Avatar src={terminal.userAvatar} name={terminal.userName} size="lg" />
                <div class="space-y-2 min-w-0">
                  <div class="flex flex-wrap gap-2">
                    <label class="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-slate-700 bg-slate-900 text-slate-300 text-xs font-mono cursor-pointer hover:bg-slate-800">
                      <Upload class="w-4 h-4" /><span>Choose picture</span>
                      <input type="file" accept="image/*" class="hidden" onchange={pickUserAvatar} />
                    </label>
                    {#if terminal.userAvatar}
                      <button onclick={() => terminal.setUserAvatar(null)} class="px-3 py-2 rounded-lg border border-slate-800 text-slate-400 text-xs font-mono hover:text-slate-200">Remove</button>
                    {/if}
                  </div>
                  <p class="text-[10px] text-slate-600 leading-relaxed">Shown on your messages. Saved with the Save button, so it follows you between devices.</p>
                  {#if userAvatarError}<p class="text-[11px] text-rose-400">{userAvatarError}</p>{/if}
                </div>
              </div>
            </div>
            <div class="space-y-1.5 flex-1 flex flex-col h-full">
              <div class="flex items-center justify-between mb-2">
                <div class="text-[10px] font-mono text-slate-500 uppercase tracking-wider">Base Rules (Applied to ALL characters)</div>
                <span class="text-[9px] font-mono text-slate-600">~{Math.max(1, Math.round(terminal.settingsDoc.data.globalSystemPrompt.length / 3.8))} tok</span>
              </div>
              <textarea bind:value={terminal.settingsDoc.data.globalSystemPrompt} class="w-full flex-1 min-h-[250px] bg-slate-900 border border-slate-800 rounded-lg p-3 text-xs text-amber-200/80 font-mono leading-relaxed outline-none resize-none focus:border-indigo-500"></textarea>
            </div>
          {:else}
            <DataPanel {terminal} />
          {/if}
        </div>

        <div class="px-5 py-4 border-t border-slate-800 bg-slate-900/50 flex items-center justify-between gap-3">
          <SaveBar doc={configTab === 'character' ? terminal.activeCharacterDoc : configTab === 'system' ? terminal.settingsDoc : undefined} />
          <button onclick={() => terminal.toggleConfig(false)} class="px-4 py-2 rounded-lg text-xs font-mono text-slate-400 hover:text-slate-200 hover:bg-slate-800">Close</button>
        </div>
      </div>
    </div>
  {/if}
</div>
