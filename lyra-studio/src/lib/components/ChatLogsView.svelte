<script lang="ts">
  import { onMount } from 'svelte';
  import { MessageSquare, Clock, RefreshCw, Database, Trash2, Edit2, Save, X, Activity, Heart, Tag } from 'lucide-svelte';
  import { stm, errorText } from '../api';

  const PAGE_SIZE = 100;

  let sessions = $state<any[]>([]);
  let messages = $state<any[]>([]);
  let activeSessionId = $state<string | null>(null);
  let isLoadingSessions = $state(false);
  let isLoadingMessages = $state(false);
  let hasMore = $state(false);
  let errorMsg = $state('');

  // View options
  let newestFirst = $state(true);
  let showDeleted = $state(false);
  let ingestFilter = $state<'all' | 'pending' | 'ingested'>('all');

  // Message Editing State
  let editingMsgId = $state<number | null>(null);
  let editForm = $state<any>({});
  let isSaving = $state(false);

  let activeSession = $derived(sessions.find((s) => s.id === activeSessionId));

  // How many rows the current view can reach, for the "X of N rows" counter.
  // null = unknown (deleted rows shown, or an older server that sends no ingestion count).
  let reachableRows = $derived.by(() => {
    if (!activeSession || showDeleted) return null;
    const total = activeSession.message_count ?? 0;
    if (ingestFilter === 'all') return total;
    const pending = activeSession.pending_ingest_count;
    if (pending == null) return null;
    return ingestFilter === 'pending' ? pending : Math.max(0, total - pending);
  });

  // Guards against a slow response from a previous session overwriting the current one
  let loadToken = 0;

  function fail(action: string, err: unknown) {
    errorMsg = `${action}: ${errorText(err)}`;
  }

  function formatDate(value: string | null | undefined) {
    return value ? new Date(value).toLocaleString() : 'never';
  }

  function formatDay(value: string | null | undefined) {
    return value ? new Date(value).toLocaleDateString() : '';
  }

  function clearMessages() {
    activeSessionId = null;
    messages = [];
    hasMore = false;
  }

  async function loadSessions() {
    isLoadingSessions = true;
    try {
      sessions = await stm('/sessions');
      errorMsg = '';
      if (activeSessionId && sessions.some((s) => s.id === activeSessionId)) {
        await loadMessages(true);
      } else if (sessions.length > 0) {
        await selectSession(sessions[0].id);
      } else {
        clearMessages();
      }
    } catch (err) {
      fail('Could not load sessions', err);
    }
    isLoadingSessions = false;
  }

  function onSessionPicked(e: Event) {
    const id = (e.currentTarget as HTMLSelectElement).value;
    if (id) selectSession(id);
  }

  async function selectSession(id: string) {
    activeSessionId = id;
    editingMsgId = null;
    await loadMessages(true);
  }

  // reset = true starts over from the first page; false appends the next page
  async function loadMessages(reset: boolean) {
    if (!activeSessionId) return;
    const token = ++loadToken;
    const offset = reset ? 0 : messages.length;
    if (reset) {
      messages = [];
      hasMore = false;
    }
    isLoadingMessages = true;
    try {
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(offset),
        order: newestFirst ? 'desc' : 'asc',
        include_deleted: String(showDeleted)
      });
      if (ingestFilter !== 'all') {
        params.set('ingested', String(ingestFilter === 'ingested'));
      }
      const page = await stm<any[]>(
        `/sessions/${encodeURIComponent(activeSessionId)}/messages?${params}`
      );
      if (token !== loadToken) return; // a newer request has taken over
      if (reset) {
        messages = page;
      } else {
        const known = new Set(messages.map((m) => m.id));
        messages = [...messages, ...page.filter((m) => !known.has(m.id))];
      }
      hasMore = page.length === PAGE_SIZE;
      errorMsg = '';
    } catch (err) {
      if (token === loadToken) fail('Could not load messages', err);
    }
    if (token === loadToken) isLoadingMessages = false;
  }

  function toggleOrder() {
    newestFirst = !newestFirst;
    loadMessages(true);
  }

  function toggleDeleted() {
    showDeleted = !showDeleted;
    loadMessages(true);
  }

  // All messages -> not ingested -> ingested -> back to all
  function cycleIngestFilter() {
    ingestFilter = ingestFilter === 'all' ? 'pending' : ingestFilter === 'pending' ? 'ingested' : 'all';
    loadMessages(true);
  }

  async function deleteSession(id: string, e: Event) {
    e.stopPropagation();
    if (!confirm('Permanently delete this entire session and all its messages?')) return;
    try {
      await stm(`/sessions/${encodeURIComponent(id)}`, 'DELETE');
      sessions = sessions.filter((s) => s.id !== id);
      if (activeSessionId === id) {
        if (sessions.length > 0) await selectSession(sessions[0].id);
        else clearMessages();
      }
    } catch (err) {
      fail('Could not delete session', err);
    }
  }

  // Keeps the session list's counts in step without a full reload.
  // A message that hasn't been ingested also moves the "not ingested" count.
  function adjustCount(msg: any, delta: number) {
    sessions = sessions.map((s) => {
      if (s.id !== activeSessionId) return s;
      const next = { ...s, message_count: Math.max(0, (s.message_count ?? 0) + delta) };
      if (!msg.is_ingested && s.pending_ingest_count != null) {
        next.pending_ingest_count = Math.max(0, s.pending_ingest_count + delta);
      }
      return next;
    });
  }

  // Soft delete: the row stays in PostgreSQL and can be restored
  async function deleteMessage(msg: any) {
    try {
      await stm(`/messages/${msg.id}`, 'DELETE');
      if (editingMsgId === msg.id) editingMsgId = null;
      if (showDeleted) {
        messages = messages.map((m) => (m.id === msg.id ? { ...m, is_deleted: true } : m));
      } else {
        messages = messages.filter((m) => m.id !== msg.id);
      }
      adjustCount(msg, -1);
    } catch (err) {
      fail('Could not delete message', err);
    }
  }

  async function restoreMessage(msg: any) {
    try {
      await stm(`/messages/${msg.id}/restore`, 'POST');
      messages = messages.map((m) => (m.id === msg.id ? { ...m, is_deleted: false } : m));
      adjustCount(msg, 1);
    } catch (err) {
      fail('Could not restore message', err);
    }
  }

  // Hard delete: only offered on messages that are already soft-deleted
  async function purgeMessage(msg: any) {
    if (!confirm('Permanently delete this message? This cannot be undone.')) return;
    try {
      await stm(`/messages/${msg.id}?hard=true`, 'DELETE');
      messages = messages.filter((m) => m.id !== msg.id);
    } catch (err) {
      fail('Could not permanently delete message', err);
    }
  }

  function startEdit(msg: any) {
    editingMsgId = msg.id;
    editForm = {
      content: msg.content,
      session_type: msg.session_type || 'casual_chat',
      ui_evaluated_tone: msg.ui_evaluated_tone || 'neutral_logistical',
      ui_stress_level: msg.ui_stress_level || 1,
      ui_closeness_level: msg.ui_closeness_level || 3,
      book_video_title: msg.book_video_title || ''
    };
  }

  // Levels must be whole numbers from 1 to 5; anything else falls back
  function clampLevel(value: unknown, fallback: number) {
    const n = parseInt(String(value), 10);
    if (Number.isNaN(n)) return fallback;
    return Math.min(5, Math.max(1, n));
  }

  async function saveMessageEdit(msg: any) {
    if (!String(editForm.content ?? '').trim()) {
      errorMsg = 'Could not save message: content cannot be empty.';
      return;
    }
    const payload = {
      content: editForm.content,
      session_type: String(editForm.session_type ?? '').trim() || 'casual_chat',
      ui_evaluated_tone: String(editForm.ui_evaluated_tone ?? '').trim() || 'neutral_logistical',
      ui_stress_level: clampLevel(editForm.ui_stress_level, msg.ui_stress_level || 1),
      ui_closeness_level: clampLevel(editForm.ui_closeness_level, msg.ui_closeness_level || 3),
      // A blank title is stored as "no title", not as an empty string
      book_video_title: String(editForm.book_video_title ?? '').trim() || null
    };
    isSaving = true;
    try {
      const result = await stm(`/messages/${msg.id}`, 'PATCH', payload);
      // Update just this row, so the list keeps its place
      messages = messages.map((m) =>
        m.id === msg.id
          ? { ...m, ...payload, token_count: result?.token_count ?? m.token_count }
          : m
      );
      editingMsgId = null;
      errorMsg = '';
    } catch (err) {
      fail('Could not save message', err);
    }
    isSaving = false;
  }

  onMount(() => {
    loadSessions();
  });
</script>

<div class="flex flex-col h-full bg-slate-950 border border-slate-800 rounded-xl overflow-hidden shadow-xl">
  {#if errorMsg}
    <div class="flex items-start justify-between gap-3 px-4 py-2 bg-rose-500/10 border-b border-rose-500/30 text-rose-300 text-xs font-mono shrink-0" role="alert">
      <span class="break-words min-w-0">{errorMsg}</span>
      <button onclick={() => errorMsg = ''} class="p-1 shrink-0 text-rose-300 hover:text-rose-100 hover:bg-rose-500/20 rounded transition-colors" aria-label="Dismiss error">
        <X class="w-3.5 h-3.5" />
      </button>
    </div>
  {/if}

  <div class="flex flex-col md:flex-row flex-1 min-h-0">
    <!-- Phones: compact session picker, so the messages get the screen -->
    <div class="md:hidden flex items-center gap-2 p-2 border-b border-slate-800 bg-slate-900/50 shrink-0">
      <select
        value={activeSessionId ?? ''}
        onchange={onSessionPicked}
        aria-label="Session"
        class="flex-1 min-w-0 bg-slate-900 border border-slate-700 rounded-md px-2 py-2 text-xs font-mono text-slate-200 focus:outline-none focus:border-sky-500"
      >
        {#if sessions.length === 0}
          <option value="">No sessions found</option>
        {/if}
        {#each sessions as sess (sess.id)}
          <option value={sess.id}>{sess.title} ({sess.message_count ?? 0})</option>
        {/each}
      </select>
      <button onclick={loadSessions} class="p-2 shrink-0 text-slate-400 hover:text-sky-400 transition-colors" aria-label="Refresh sessions and messages" title="Refresh">
        <RefreshCw class="w-4 h-4 {isLoadingSessions ? 'animate-spin' : ''}" />
      </button>
      {#if activeSessionId}
        <button onclick={(e) => deleteSession(activeSessionId ?? '', e)} class="p-2 shrink-0 text-slate-400 hover:text-rose-400 hover:bg-rose-500/20 rounded transition-colors" aria-label="Permanently delete this session" title="Purge Session">
          <Trash2 class="w-4 h-4" />
        </button>
      {/if}
    </div>

    <!-- Wide screens: sessions sidebar -->
    <div class="hidden md:flex md:w-1/3 md:max-w-sm shrink-0 border-r border-slate-800 bg-slate-900/30 flex-col">
      <div class="p-3 border-b border-slate-800 flex items-center justify-between bg-slate-900/50 shrink-0">
        <span class="text-xs font-mono font-semibold text-slate-300">Active Sessions</span>
        <button onclick={loadSessions} class="p-1 text-slate-400 hover:text-sky-400 transition-colors" aria-label="Refresh sessions and messages" title="Refresh">
          <RefreshCw class="w-3.5 h-3.5 {isLoadingSessions ? 'animate-spin' : ''}" />
        </button>
      </div>

      <div class="flex-1 overflow-y-auto p-2 space-y-1">
        {#if sessions.length === 0 && !isLoadingSessions}
          <div class="p-4 text-center text-xs font-mono text-slate-500">No sessions found.</div>
        {/if}

        {#each sessions as sess (sess.id)}
          <div class="relative group">
            <button
              onclick={() => selectSession(sess.id)}
              class="w-full text-left p-3 rounded-lg flex flex-col gap-1.5 transition-all border {activeSessionId === sess.id ? 'bg-sky-500/10 border-sky-500/30 text-sky-300' : 'border-transparent text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'}"
            >
              <div class="flex items-center gap-2 pr-8 truncate">
                <MessageSquare class="w-3.5 h-3.5 shrink-0" />
                <span class="text-xs font-mono font-medium truncate">{sess.title}</span>
              </div>
              <div class="text-[9px] text-slate-500 font-mono ml-5 truncate">ID: {sess.id}</div>
              <div class="text-[9px] text-slate-500 font-mono ml-5">
                {sess.message_count ?? 0} messages{#if sess.pending_ingest_count != null}, {sess.pending_ingest_count} not ingested{/if}
              </div>
              <div class="flex items-center gap-1.5 text-[9px] font-mono opacity-70 ml-5 mt-0.5" title="Last message">
                <Clock class="w-3 h-3 shrink-0" />
                <span>{formatDate(sess.last_message_at ?? sess.created_at)}</span>
              </div>
            </button>

            <button
              onclick={(e) => deleteSession(sess.id, e)}
              class="absolute top-3 right-2 p-1.5 text-slate-500 hover:text-rose-400 hover:bg-rose-500/20 rounded opacity-60 group-hover:opacity-100 transition-all"
              title="Purge Session"
              aria-label="Permanently delete session"
            >
              <Trash2 class="w-3.5 h-3.5" />
            </button>
          </div>
        {/each}
      </div>
    </div>

    <!-- Message Rows -->
    <div class="flex-1 flex flex-col min-h-0 min-w-0 bg-slate-950/50 relative">
      <div class="p-2 md:p-3 border-b border-slate-800 bg-slate-900/50 flex flex-wrap items-center justify-between gap-2 shrink-0">
        <span class="text-xs font-mono text-slate-400 truncate min-w-0">
          Table: <span class="text-emerald-400">short_term_memory.messages</span>
        </span>
        <div class="flex flex-wrap items-center gap-2">
          <button
            onclick={toggleOrder}
            class="px-2 py-1 rounded border border-slate-700 bg-slate-800 text-[10px] font-mono text-slate-300 hover:bg-slate-700 transition-colors"
            title="Switch message order"
          >
            {newestFirst ? 'Newest first' : 'Oldest first'}
          </button>
          <button
            onclick={toggleDeleted}
            aria-pressed={showDeleted}
            class="px-2 py-1 rounded border text-[10px] font-mono transition-colors {showDeleted ? 'bg-rose-500/20 border-rose-500/40 text-rose-300' : 'bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700'}"
          >
            {showDeleted ? 'Hide deleted' : 'Show deleted'}
          </button>
          <button
            onclick={cycleIngestFilter}
            class="px-2 py-1 rounded border text-[10px] font-mono transition-colors {ingestFilter === 'all' ? 'bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700' : 'bg-indigo-500/20 border-indigo-500/40 text-indigo-300'}"
            title="Filter by ingestion into the long-term vault"
          >
            {ingestFilter === 'all' ? 'All messages' : ingestFilter === 'pending' ? 'Not ingested only' : 'Ingested only'}
          </button>
          <span class="px-2 py-0.5 rounded-full bg-slate-800 border border-slate-700 text-[10px] text-slate-300 font-mono">
            {messages.length}{#if reachableRows !== null} of {reachableRows}{/if} rows
          </span>
          {#if activeSession && activeSession.pending_ingest_count != null}
            {#if activeSession.pending_ingest_count > 0}
              <span class="px-2 py-0.5 rounded-full bg-amber-500/10 border border-amber-500/30 text-[10px] text-amber-400 font-mono" title="Messages in this session waiting to be ingested into the long-term vault">
                {activeSession.pending_ingest_count} not ingested
              </span>
            {:else if (activeSession.message_count ?? 0) > 0}
              <span class="px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-[10px] text-emerald-400 font-mono">
                all ingested
              </span>
            {/if}
          {/if}
        </div>
      </div>

      <div class="flex-1 overflow-y-auto overflow-x-hidden p-2 md:p-4 space-y-3 md:space-y-4">
        {#if messages.length === 0}
          <div class="h-full flex flex-col items-center justify-center text-slate-600 font-mono text-xs gap-2">
            <Database class="w-6 h-6 opacity-50" />
            {#if isLoadingMessages}
              Loading messages...
            {:else if !activeSessionId}
              Select a session to view message logs
            {:else}
              No messages in this session
            {/if}
          </div>
        {/if}

        {#each messages as msg (msg.id)}
          <div class="p-3 md:p-4 rounded-xl border bg-slate-900/40 flex flex-col gap-3 group transition-colors {msg.is_deleted ? 'border-rose-500/30' : 'border-slate-800 hover:border-slate-700'}">
            <div class="flex flex-wrap items-center justify-between gap-2">
              <div class="flex flex-wrap items-center gap-3">
                <span class="px-2 py-0.5 rounded text-[10px] font-mono uppercase tracking-wider font-bold
                  {msg.role === 'user' ? 'bg-sky-500/20 text-sky-400 border border-sky-500/30' :
                   msg.role === 'assistant' ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' :
                   'bg-slate-700 text-slate-300 border border-slate-600'}">
                  {msg.role}
                </span>
                <span class="text-[10px] font-mono text-slate-500">ID: {msg.id} • {msg.name ?? 'unnamed'}</span>
                <span class="text-[10px] font-mono text-slate-600">{formatDate(msg.created_at)}</span>
                {#if msg.is_deleted}
                  <span class="px-1.5 py-0.5 rounded text-[9px] font-mono bg-rose-500/10 text-rose-400 border border-rose-500/30">deleted</span>
                {/if}
              </div>

              <div class="flex items-center gap-3">
                <span class="text-[9px] font-mono text-slate-600">{msg.token_count} tok</span>
                <div class="flex items-center gap-1 opacity-60 group-hover:opacity-100 transition-opacity">
                  {#if msg.is_deleted}
                    <button onclick={() => restoreMessage(msg)} class="px-2 py-1 text-[10px] font-mono text-emerald-400 hover:bg-emerald-500/20 rounded transition-colors">
                      Restore
                    </button>
                    <button onclick={() => purgeMessage(msg)} class="px-2 py-1 text-[10px] font-mono text-rose-400 hover:bg-rose-500/20 rounded transition-colors">
                      Delete forever
                    </button>
                  {:else}
                    <button onclick={() => startEdit(msg)} class="p-1.5 text-slate-500 hover:text-sky-400 hover:bg-sky-500/20 rounded transition-colors" title="Edit Metadata" aria-label="Edit message">
                      <Edit2 class="w-3.5 h-3.5" />
                    </button>
                    <button onclick={() => deleteMessage(msg)} class="p-1.5 text-slate-500 hover:text-rose-400 hover:bg-rose-500/20 rounded transition-colors" title="Delete Message (can be restored)" aria-label="Delete message">
                      <Trash2 class="w-3.5 h-3.5" />
                    </button>
                  {/if}
                </div>
              </div>
            </div>

            {#if editingMsgId === msg.id}
              <!-- Inline Edit Form -->
              <div class="flex flex-col gap-3 mt-1 p-3 bg-slate-950/50 rounded-lg border border-slate-800">
                <textarea
                  bind:value={editForm.content}
                  rows="3"
                  aria-label="Message content"
                  class="w-full bg-slate-900 border border-slate-700 rounded-md px-3 py-2 text-xs font-mono text-slate-200 focus:outline-none focus:border-sky-500"
                ></textarea>

                <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <div class="flex flex-col gap-1.5">
                    <label class="text-[9px] font-mono text-slate-500 uppercase">
                      Tone
                      <input type="text" bind:value={editForm.ui_evaluated_tone} class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs font-mono text-slate-300 focus:border-sky-500 outline-none" />
                    </label>
                  </div>
                  <div class="flex flex-col gap-1.5">
                    <label class="text-[9px] font-mono text-slate-500 uppercase">
                      Session Type
                      <input type="text" bind:value={editForm.session_type} class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs font-mono text-slate-300 focus:border-sky-500 outline-none" />
                    </label>
                  </div>
                  <div class="flex flex-col gap-1.5">
                    <label class="text-[9px] font-mono text-slate-500 uppercase">
                      Stress (1-5)
                      <input type="number" min="1" max="5" bind:value={editForm.ui_stress_level} class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs font-mono text-slate-300 focus:border-sky-500 outline-none" />
                    </label>
                  </div>
                  <div class="flex flex-col gap-1.5">
                    <label class="text-[9px] font-mono text-slate-500 uppercase">
                      Closeness (1-5)
                      <input type="number" min="1" max="5" bind:value={editForm.ui_closeness_level} class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs font-mono text-slate-300 focus:border-sky-500 outline-none" />
                    </label>
                  </div>
                </div>

                <div class="flex flex-col gap-1.5 mt-1">
                  <label class="text-[9px] font-mono text-slate-500 uppercase">
                    Media Title (Book/Video)
                    <input type="text" bind:value={editForm.book_video_title} placeholder="Leave blank if not applicable" class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs font-mono text-slate-300 focus:border-sky-500 outline-none" />
                  </label>
                </div>

                <div class="flex items-center justify-end gap-2 mt-2 pt-2 border-t border-slate-800">
                  <button onclick={() => editingMsgId = null} class="px-3 py-1.5 text-[10px] text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded transition-colors uppercase tracking-wider font-bold">
                    Cancel
                  </button>
                  <button onclick={() => saveMessageEdit(msg)} disabled={isSaving} class="flex items-center gap-1.5 px-4 py-1.5 bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-white rounded text-[10px] uppercase tracking-wider font-bold transition-colors shadow-sm">
                    <Save class="w-3 h-3" />
                    {isSaving ? 'Saving...' : 'Save Meta'}
                  </button>
                </div>
              </div>
            {:else}
              <!-- Standard View -->
              <p class="text-xs font-mono whitespace-pre-wrap break-words leading-relaxed {msg.is_deleted ? 'text-slate-500' : 'text-slate-300'}">
                {msg.content}
              </p>

              <!-- Metadata Badges -->
              <div class="flex flex-wrap items-center gap-2 mt-1 pt-3 border-t border-slate-800/50">
                {#if msg.session_type}
                  <div class="flex items-center gap-1 px-1.5 py-0.5 bg-slate-800/80 rounded text-[9px] font-mono text-slate-400 border border-slate-700">
                    <Tag class="w-2.5 h-2.5" />
                    {msg.session_type}
                  </div>
                {/if}
                {#if msg.ui_evaluated_tone}
                  <div class="flex items-center gap-1 px-1.5 py-0.5 bg-purple-500/10 rounded text-[9px] font-mono text-purple-400 border border-purple-500/20">
                    {msg.ui_evaluated_tone}
                  </div>
                {/if}
                {#if msg.book_video_title}
                  <div class="flex items-center gap-1 px-1.5 py-0.5 bg-sky-500/10 rounded text-[9px] font-mono text-sky-400 border border-sky-500/20" title="Media title">
                    {msg.book_video_title}
                  </div>
                {/if}
                <div class="flex items-center gap-1 px-1.5 py-0.5 bg-rose-500/10 rounded text-[9px] font-mono text-rose-400 border border-rose-500/20" title="Stress Level">
                  <Activity class="w-2.5 h-2.5" />
                  Lvl {msg.ui_stress_level || 1}
                </div>
                <div class="flex items-center gap-1 px-1.5 py-0.5 bg-pink-500/10 rounded text-[9px] font-mono text-pink-400 border border-pink-500/20" title="Closeness Level">
                  <Heart class="w-2.5 h-2.5" />
                  Lvl {msg.ui_closeness_level || 3}
                </div>
                {#if msg.is_ingested}
                  <div class="px-1.5 py-0.5 bg-emerald-500/10 rounded text-[9px] font-mono text-emerald-400 border border-emerald-500/20" title="Ingested into the long-term vault: {formatDate(msg.ingested_at)}">
                    ingested {formatDay(msg.ingested_at)}
                  </div>
                {:else}
                  <div class="px-1.5 py-0.5 bg-slate-800/80 rounded text-[9px] font-mono text-slate-500 border border-slate-700" title="Not yet ingested into the long-term vault">
                    not ingested
                  </div>
                {/if}
              </div>
            {/if}
          </div>
        {/each}

        {#if hasMore}
          <button
            onclick={() => loadMessages(false)}
            disabled={isLoadingMessages}
            class="w-full py-2 rounded-lg border border-slate-800 bg-slate-900/40 text-xs font-mono text-slate-300 hover:bg-slate-800 disabled:opacity-50 transition-colors"
          >
            {isLoadingMessages ? 'Loading...' : newestFirst ? 'Load older messages' : 'Load newer messages'}
          </button>
        {/if}
      </div>
    </div>
  </div>
</div>
