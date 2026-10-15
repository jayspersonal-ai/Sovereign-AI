import type {
  Message, Session, WorkspaceItem, StreamChunk, BackendStatus,
  CharacterCardV2, LoreNode, LorePage, Lorebook, SettingsData, ChatAttachment
} from './types';
import { SyncedDoc, dataApi } from './persist.svelte';
import { BACKUP_FORMAT, plain, slugify, uniqueId } from './backup';
import { normalizeWorkspaceItem } from './media';
import { STAMP_TOKENS, STAMP_USER_MESSAGES, resolveTimeMacros, stampContent } from './timestamps';
import { IMAGE_TOKEN_ESTIMATE, blobToDataUrl, composeWithAttachments, prepareImage, toModelContent } from './attachments';
import { avatarFromExtensions, blobToAvatar, safeAvatar, urlToAvatar } from './avatar';
import type { AppBackup, RestoreResult, Counts } from './backup';

// Small UI state shared between devices (data/state.json). It saves immediately; there is no Save button.
interface SharedState {
  activeCharacterId: string;
  activeLorebookId: string;
  sessionLorebook: Record<string, boolean>; // per-session "inject lorebook" switch
  sessionTitles: Record<string, string>; // custom session names (the database has no rename route)
  activeModel: string; // the model chosen in the top bar; shared so both devices use the same one
}

const DEFAULT_GLOBAL_PROMPT = 'CRITICAL DIRECTIVE: You possess native reasoning capabilities. When evaluating if a tool is required, you must observe these strict behavioral rules. 1. Grounding: You are only permitted to invoke tools that are explicitly defined in the active function configuration payload. Do not invent names or guess argument footprints. 2. Verifcation: If your internal reasoning trace determines that an action is necessary, check the argument layout carefully. If a required value is missing from the chat history, do not hallucinate a placeholder; instead, ask the user for clarification. 3. Execution: Output tool cals cleanly using the specified JSON schema format. Do not append conversational chatter inside or around the functional invocation. block.';

function newCard(name: string, description: string): CharacterCardV2 {
  return {
    spec: 'chara_card_v2',
    spec_version: '2.0',
    data: {
      name, description,
      personality: '', scenario: '', first_mes: '', mes_example: '',
      creator_notes: '', system_prompt: '', post_history_instructions: '',
      alternate_greetings: [], tags: [], creator: '', character_version: '1.0', extensions: {}
    }
  };
}

// Cards from other frontends use {{char}} / {{user}} placeholders. There is no user-name setting yet, so {{user}} becomes "User".
function applyMacros(text: string | undefined, charName: string, userName = 'User'): string {
  return (text ?? '').replace(/\{\{char\}\}/gi, charName).replace(/\{\{user\}\}/gi, userName);
}

// The model's whole window (your llama.cpp -c value). The conversation may fill all of it EXCEPT the room the next turn needs:
// the AI's thinking plus reply (measured from her last few turns), and one image she might open. Whenever the count passes
// that, the oldest messages are dropped, just enough to get back under it.
const CONTEXT_WINDOW = 32768;
const IMAGE_RESERVE = 1200; // one image opened with the view tool (about 1,100)
const RESERVE_MARGIN = 400;
const MIN_RESERVE = 3500;
const MAX_RESERVE = 12000;
const DEFAULT_RESERVE = 5500; // until a few replies have been measured
const COMPLETIONS_KEY = 'recent_completions';
function readCompletions(): number[] {
  try {
    const v = JSON.parse((typeof localStorage !== 'undefined' && localStorage.getItem(COMPLETIONS_KEY)) || '[]');
    return Array.isArray(v) ? v.filter((n) => typeof n === 'number' && n > 0 && n < 100000).slice(-5) : [];
  } catch {
    return [];
  }
}

const OVERHEAD_KEY = 'context_overhead';
function readOverhead(): number {
  try {
    const n = Number(typeof localStorage !== 'undefined' ? localStorage.getItem(OVERHEAD_KEY) : 0);
    return Number.isFinite(n) && n > 0 && n < 100000 ? Math.round(n) : 0;
  } catch {
    return 0;
  }
}
function writeOverhead(n: number) {
  try {
    localStorage.setItem(OVERHEAD_KEY, String(n));
  } catch { /* storage unavailable: it is measured again after the next reply */ }
}

// Before shared state existed, each session's lorebook on/off choice was kept per device. This is read once to carry it over.
const SESSION_LORE_KEY = 'session_lorebook';
function readSessionLore(): Record<string, boolean> {
  try {
    if (typeof localStorage === 'undefined') return {};
    const raw = JSON.parse(localStorage.getItem(SESSION_LORE_KEY) ?? '{}');
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {};
    return Object.fromEntries(Object.entries(raw).filter(([, v]) => typeof v === 'boolean')) as Record<string, boolean>;
  } catch {
    return {};
  }
}

// Model names that can't hold a conversation (embeddings, speech, ...). They stay out of the picker.
const NON_CHAT_MODEL = /embed|rerank|whisper|tts|moderation|speech/i;

// The page the app opens on. It is a real conversation in the database like any other.
const DEFAULT_SESSION_ID = 'session-default-5070ti';

function formatStamp(d: Date): string {
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

// The database titles sessions "Session <id>" when none is given. Show something readable instead.
function friendlySessionTitle(id: string, dbTitle: string | null | undefined, createdAt?: string | null): string {
  const title = (dbTitle ?? '').trim();
  if (title && title !== `Session ${id}`) return title;
  if (id === DEFAULT_SESSION_ID) return 'Default session';
  const stamp = /^session-(\d{12,})$/.exec(id)?.[1];
  const when = stamp ? new Date(Number(stamp)) : createdAt ? new Date(createdAt) : null;
  return when && !isNaN(when.getTime()) ? `Session \u00b7 ${formatStamp(when)}` : id;
}

function findPage(nodes: LoreNode[], id: string | null): LorePage | null {
  if (!id) return null;
  for (const n of nodes) {
    if (n.type === 'page') {
      if (n.id === id) return n;
    } else {
      const hit = findPage(n.children ?? [], id);
      if (hit) return hit;
    }
  }
  return null;
}

// Folder open/closed state is a per-device UI preference, so it is not saved in the lorebook file.
function stripExpanded(nodes: LoreNode[]): LoreNode[] {
  return nodes.map((n) => {
    if (n.type === 'folder') {
      const { isExpanded: _ignored, ...rest } = n;
      return { ...rest, children: stripExpanded(n.children ?? []) };
    }
    return n;
  });
}

export class TerminalState {
  // --- Persona & Config State (plain JSON files via the data server) ---
  settingsDoc = new SyncedDoc<SettingsData>({
    collection: 'settings',
    defaults: { globalSystemPrompt: DEFAULT_GLOBAL_PROMPT }
  });
  stateDoc = new SyncedDoc<SharedState>({
    collection: 'state',
    defaults: { activeCharacterId: '', activeLorebookId: '', sessionLorebook: {}, sessionTitles: {}, activeModel: '' }
  });
  characterDocs = $state<SyncedDoc<CharacterCardV2>[]>([]);
  lorebookDocs = $state<SyncedDoc<Lorebook>[]>([]);
  dataLoaded = $state<boolean>(false);

  // Which character and which lorebook are active is shared between devices through data/state.json.
  // This device's last choice is also kept locally, so the right character shows at once while the shared state loads.
  private localCharacterId = $state<string>(
    typeof window !== 'undefined' ? localStorage.getItem('active_character_id') || '' : ''
  );
  get activeCharacterId(): string {
    return this.stateDoc.data.activeCharacterId || this.localCharacterId;
  }
  set activeCharacterId(id: string) {
    this.localCharacterId = id;
    try {
      if (id) localStorage.setItem('active_character_id', id);
    } catch { /* storage unavailable: the shared copy still works */ }
    if (this.stateDoc.data.activeCharacterId === id) return;
    this.updateState((s) => {
      s.activeCharacterId = id;
    });
  }
  get activeLorebookId(): string {
    return this.stateDoc.data.activeLorebookId ?? '';
  }
  set activeLorebookId(id: string) {
    if (this.stateDoc.data.activeLorebookId === id) return;
    this.updateState((s) => {
      s.activeLorebookId = id;
    });
  }

  isConfigOpen = $state<boolean>(false);

  activeCharacterDoc = $derived(
    this.characterDocs.find((d) => d.id === this.activeCharacterId) ?? this.characterDocs[0]
  );
  characterName = $derived(this.activeCharacterDoc?.data?.data?.name || 'Assistant');
  /** The active character's profile picture (a safe image data URL), or null. */
  characterAvatar = $derived(avatarFromExtensions(this.activeCharacterDoc?.data?.data?.extensions));

  /** The name typed in settings, or '' if there isn't one. */
  get userNameSetting(): string {
    return (this.settingsDoc.data.userName ?? '').trim();
  }
  /** Name used for {{user}} in cards. */
  /** Your own profile picture (kept in the settings, so it follows you between devices). */
  get userAvatar(): string | null {
    return safeAvatar(this.settingsDoc.data.userAvatar);
  }
  /** Set (or with null, remove) your picture. Saved with the settings' Save button, like your name. */
  setUserAvatar(dataUrl: string | null) {
    if (dataUrl) this.settingsDoc.data.userAvatar = dataUrl;
    else delete this.settingsDoc.data.userAvatar;
  }
  async setUserAvatarFromFile(file: Blob) {
    this.setUserAvatar(await blobToAvatar(file));
  }

  get userName(): string {
    return this.userNameSetting || 'User';
  }

  // --- Lorebook Client-Side View State ---
  isLorebookViewOpen = $state<boolean>(false);
  activeLorebookDoc = $derived(
    this.lorebookDocs.find((d) => d.id === this.activeLorebookId) ?? this.lorebookDocs[0]
  );
  activeLorePageId = $state<string | null>(null);
  activeLorePage = $derived(findPage(this.activeLorebookDoc?.data?.nodes ?? [], this.activeLorePageId));

  // --- Core Terminal State ---
  messages = $state<Message[]>([]);
  sessions = $state<Session[]>([
    {
      id: 'Lyra',
      title: 'Session 01',
      updatedAt: new Date(),
      messageCount: 1,
      lorebookEnabled: true
    },
    {
      id: 'Lameria',
      title: 'Session 02',
      updatedAt: new Date(Date.now() - 3600000),
      messageCount: 1,
      lorebookEnabled: false
    }
  ]);

  activeSessionId = $state<string>(DEFAULT_SESSION_ID);
  isStreaming = $state<boolean>(false);
  isWorkspaceOpen = $state<boolean>(false);
  workspaceItems = $state<WorkspaceItem[]>([]);
  // When the AI views something, open the Inspector for it. Per device; turn off to have viewed items just join the list.
  autoOpenViewed = $state<boolean>(
    typeof window === 'undefined' ? true : localStorage.getItem('workspace_auto_open_viewed') !== 'false'
  );
  activeWorkspaceItem = $state<WorkspaceItem | null>(null);
  allowSimulationFallback = $state<boolean>(true);

  backendStatus = $state<BackendStatus>({
    online: true,
    model: 'gemma-4-31b-it'
  });

  private abortController: AbortController | null = null;
  private apiBaseUrl: string =
    (import.meta.env.VITE_GATEWAY_URL as string | undefined) ??
    (typeof window !== 'undefined' && window.location.port === '8100' ? '/v1' : 'http://localhost:8401/v1');

  private sessionHistories = new Map<string, Message[]>();

  // Where each session's title comes from when there is no custom name: the built-in titles for the two original
  // sessions, otherwise a readable version of the database title.
  private baseTitles = new Map<string, string>();
  private seedTitles = new Map<string, string>();
  // Until now the default page showed the first built-in session's lorebook switch. It keeps that value when it becomes its own entry.
  private defaultFlagSource = { id: '', enabled: false };

  constructor() {
    this.seedTitles = new Map(this.sessions.map((s) => [s.id, s.title]));
    this.baseTitles = new Map(this.seedTitles);
    this.defaultFlagSource = { id: this.sessions[0]?.id ?? '', enabled: !!this.sessions[0]?.lorebookEnabled };
    this.sessionHistories.set(DEFAULT_SESSION_ID, [...this.messages]);
    this.syncActiveSessionMessageCount();

    // Apply shared per-session lorebook switches and titles. Also re-runs when another device's change arrives.
    $effect(() => this.applySharedSessionState());

    // Pick up sessions created on the other device when this tab comes back into view.
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
          void this.loadSessions();
          void this.loadModels();
        }
      });
    }
  }

  systemPrompt = $derived(this.buildSystemPrompt());
  systemPromptTokens = $derived(Math.max(1, Math.round(this.systemPrompt.length / 3.8)));

  // Our own count (characters / 3.8) misses what the model also receives: the tool list, recalled memories, and the real
  // tokenizer's differences. After every reply the model server reports the true prompt size; the gap between that and
  // our count is kept here and added to the estimate, so trimming happens at the real size. Remembered between visits.
  private workspaceEventCount = 0; // workspace items that arrived from the gateway (a sign the AI used a view/read tool)
  contextOverhead = $state<number>(readOverhead());
  // Tokens used by her last few replies (thinking included), as the model server counted them.
  private recentCompletions = $state<number[]>(readCompletions());

  /** How much conversation to keep: the whole window minus the room the next turn will need. */
  get contextLimit(): number {
    const biggest = this.recentCompletions.length ? Math.max(...this.recentCompletions) : 0;
    const reserve = biggest
      ? Math.min(MAX_RESERVE, Math.max(MIN_RESERVE, Math.ceil(biggest * 1.15) + IMAGE_RESERVE + RESERVE_MARGIN))
      : DEFAULT_RESERVE;
    return CONTEXT_WINDOW - reserve;
  }

  private recordCompletion(tokens: number, wasCutOff: boolean) {
    // A reply that was cut off used less than it wanted, so count it as bigger to make room next time.
    const n = wasCutOff ? Math.ceil(tokens * 1.5) + 1000 : tokens;
    this.recentCompletions = [...this.recentCompletions, n].slice(-5);
    try { localStorage.setItem(COMPLETIONS_KEY, JSON.stringify(this.recentCompletions)); } catch { /* measured again next reply */ }
  }
  lastPromptTokens = $state<number>(0); // the exact size of the last request, as the model server counted it

  /** Keep the gap between the server's exact count and ours. */
  private calibrate(serverPromptTokens: number, ourEstimate: number) {
    this.contextOverhead = Math.max(0, Math.round(serverPromptTokens - ourEstimate));
    writeOverhead(this.contextOverhead);
  }

  private estimatedTokens(): number {
    return this.systemPromptTokens + this.messages.reduce((acc: number, m: Message) => acc + (m.tokens || Math.max(1, Math.round(m.content.length / 3.8))) + (STAMP_USER_MESSAGES && m.role === 'user' ? STAMP_TOKENS : 0), 0);
  }

  totalTokenCount = $derived(
    this.systemPromptTokens +
      this.messages.reduce((acc: number, m: Message) => acc + (m.tokens || Math.max(1, Math.round(m.content.length / 3.8))) + (STAMP_USER_MESSAGES && m.role === 'user' ? STAMP_TOKENS : 0), 0) +
      this.contextOverhead
  );

  activeSession = $derived(
    this.sessions.find((s) => s.id === this.activeSessionId) || this.sessions[0]
  );

  private syncActiveSessionMessageCount() {
    if (this.activeSession) {
      this.activeSession.messageCount = this.messages.length;
      this.activeSession.updatedAt = new Date();
    }
  }

  toggleLorebookView(force?: boolean) {
    this.isLorebookViewOpen = force !== undefined ? force : !this.isLorebookViewOpen;
    if (this.isLorebookViewOpen) {
      this.isConfigOpen = false;
      this.isWorkspaceOpen = false;
    }
  }

  toggleSessionLorebook(enabled: boolean) {
    const session = this.activeSession;
    if (!session) return;
    session.lorebookEnabled = enabled;

    const id = session.id;
    this.updateState((s) => {
      s.sessionLorebook = { ...(s.sessionLorebook ?? {}), [id]: enabled };
    });
  }

  /** Copy the shared per-session switches and custom titles onto the session list. */
  applySharedSessionState() {
    const savedModel = this.stateDoc.data.activeModel;
    if (typeof savedModel === 'string' && savedModel && savedModel !== this.backendStatus.model) this.backendStatus.model = savedModel;

    const flags = this.stateDoc.data.sessionLorebook ?? {};
    const titles = this.stateDoc.data.sessionTitles ?? {};
    for (const session of this.sessions) {
      const flag = flags[session.id];
      if (typeof flag === 'boolean') session.lorebookEnabled = flag;
      const custom = titles[session.id];
      session.title = (typeof custom === 'string' && custom.trim()) || this.baseTitles.get(session.id) || session.id;
    }
  }

  // --- Shared state: saved straight away, merged with other devices' changes ---

  private pendingState: Array<(s: SharedState) => void> = [];
  private stateFlushing = false;

  /** Change the shared state and save it. If another device saved first, its changes are kept and ours re-applied on top. */
  updateState(mutate: (s: SharedState) => void) {
    mutate(this.stateDoc.data);
    this.pendingState.push(mutate);
    void this.flushState();
  }

  private async flushState() {
    if (this.stateFlushing) return; // the running flush picks up anything queued meanwhile
    this.stateFlushing = true;
    try {
      for (let attempt = 0; attempt < 4 && this.pendingState.length > 0; attempt++) {
        const included = this.pendingState.length;
        await this.stateDoc.save();
        const status = this.stateDoc.status;
        if (status === 'conflict') {
          this.stateDoc.useTheirs();
          for (const mutate of this.pendingState) mutate(this.stateDoc.data);
          continue;
        }
        if (status === 'error') break; // offline or server error: stays queued, and the draft is kept locally
        this.pendingState.splice(0, included);
      }
    } catch (e) {
      console.error('[Terminal] shared state save failed:', e);
    } finally {
      this.stateFlushing = false;
    }
  }

  private async loadState() {
    try {
      const doc = this.stateDoc;
      await doc.load();
      if (doc.status === 'conflict') doc.useTheirs(); // an old local draft lost to a newer saved copy
      const s = doc.data;
      if (typeof s.activeCharacterId !== 'string') s.activeCharacterId = '';
      if (typeof s.activeLorebookId !== 'string') s.activeLorebookId = '';
      if (!s.sessionLorebook || typeof s.sessionLorebook !== 'object') s.sessionLorebook = {};
      if (!s.sessionTitles || typeof s.sessionTitles !== 'object' || Array.isArray(s.sessionTitles)) s.sessionTitles = {};
      if (typeof s.activeModel !== 'string') s.activeModel = '';

      // First run with shared state: carry over what this device had stored before.
      if (!s.activeCharacterId && this.localCharacterId) s.activeCharacterId = this.localCharacterId;
      if (!s.activeLorebookId) {
        const legacy = typeof localStorage !== 'undefined' ? localStorage.getItem('active_lorebook_id') : null;
        if (legacy) s.activeLorebookId = legacy;
      }
      if (Object.keys(s.sessionLorebook).length === 0) Object.assign(s.sessionLorebook, readSessionLore());

      if (doc.needsSave) await doc.save();
      this.applySharedSessionState();
    } catch (e) {
      console.error('[Terminal] shared state load failed:', e);
    }
  }

  getAllActivePages(nodes: LoreNode[] = this.activeLorebookDoc?.data?.nodes ?? []): LorePage[] {
    let pages: LorePage[] = [];
    for (const node of nodes) {
      if (node.type === 'page') {
        if (node.isActive) pages.push(node);
      } else if (node.type === 'folder' && node.children) {
        pages = [...pages, ...this.getAllActivePages(node.children)];
      }
    }
    return pages;
  }

  // --- File-backed data: character cards, lorebooks, settings ---

  private readIndex(collection: string): string[] {
    try { return JSON.parse(localStorage.getItem(`index:${collection}`) ?? '[]'); } catch { return []; }
  }
  private writeIndex(collection: string, ids: string[]) {
    try { localStorage.setItem(`index:${collection}`, JSON.stringify(ids)); } catch { /* ignore */ }
  }

  // The server's list is the truth when reachable; the local index only covers being offline.
  private async listIds(collection: 'characters' | 'lorebooks'): Promise<string[]> {
    try {
      const items = await dataApi.list<unknown>(collection);
      const ids = items.map((i) => i.id as string);
      this.writeIndex(collection, ids);
      return ids;
    } catch {
      return this.readIndex(collection);
    }
  }

  async loadData() {
    try {
      await this.loadState(); // first: the lorebook checks below depend on the stored selection
      // allSettled: a failure in one area (say, lorebooks) must not stop the others or the chat from loading.
      const results = await Promise.allSettled([this.settingsDoc.load(), this.loadCharacters(), this.loadLorebooks(), this.loadSessions(), this.loadModels()]);
      for (const r of results) {
        if (r.status === 'rejected') console.error('[Terminal] data load failed:', r.reason);
      }
      // First run: write the default settings file so there is something on disk to edit.
      if (this.settingsDoc.status === 'clean' && this.settingsDoc.needsSave) await this.settingsDoc.save();
    } finally {
      this.dataLoaded = true;
    }
  }

  async loadCharacters() {
    const ids = await this.listIds('characters');
    const docs = ids.map((id) => new SyncedDoc<CharacterCardV2>({ collection: 'characters', id, defaults: newCard('Assistant', '') }));
    await Promise.all(docs.map((d) => d.load()));
    this.characterDocs = docs;
    if (docs.length === 0) {
      await this.createNewCharacter('Assistant', 'You are Assistant, a helpful AI running on local hardware.');
      return;
    }
    if (!docs.some((d) => d.id === this.activeCharacterId)) this.activeCharacterId = docs[0].id!;
  }

  async createNewCharacter(name: string = 'New Character', persona: string = '') {
    const id = dataApi.newId('char');
    const doc = new SyncedDoc<CharacterCardV2>({ collection: 'characters', id, defaults: newCard(name, persona) });
    await doc.load(); // not on the server yet: starts from the defaults
    this.writeIndex('characters', [...this.readIndex('characters'), id]);
    this.characterDocs = [...this.characterDocs, doc];
    this.activeCharacterId = id;
    await doc.save();
  }

  async deleteActiveCharacter() {
    const doc = this.activeCharacterDoc;
    if (!doc?.id || this.characterDocs.length <= 1) return;
    try {
      await dataApi.remove('characters', doc.id);
    } catch (e) {
      console.error('[Terminal] Failed to delete character file', e);
      return;
    }
    doc.dispose();
    this.writeIndex('characters', this.readIndex('characters').filter((x) => x !== doc.id));
    this.characterDocs = this.characterDocs.filter((d) => d !== doc);
    this.activeCharacterId = this.characterDocs[0].id!;
  }

  async loadLorebooks() {
    const ids = await this.listIds('lorebooks');
    const docs = ids.map((id) => new SyncedDoc<Lorebook>({ collection: 'lorebooks', id, defaults: { name: 'Lorebook', nodes: [] } }));
    await Promise.all(docs.map((d) => d.load()));
    this.lorebookDocs = docs;
    // First run: carry over the lorebook that used to live in this browser's localStorage.
    if (docs.length === 0) await this.createLorebook('Main Lorebook', this.legacyLoreNodes());
    if (!this.lorebookDocs.some((d) => d.id === this.activeLorebookId)) this.activeLorebookId = this.lorebookDocs[0]?.id ?? '';
  }

  private legacyLoreNodes(): LoreNode[] {
    try {
      const raw = localStorage.getItem('lorebook_tree');
      return raw ? stripExpanded(JSON.parse(raw)) : [];
    } catch {
      return [];
    }
  }

  async createLorebook(name: string = 'New Lorebook', nodes: LoreNode[] = []) {
    const id = dataApi.newId('lore');
    const doc = new SyncedDoc<Lorebook>({ collection: 'lorebooks', id, defaults: { name, nodes } });
    await doc.load();
    this.writeIndex('lorebooks', [...this.readIndex('lorebooks'), id]);
    this.lorebookDocs = [...this.lorebookDocs, doc];
    this.activeLorebookId = id;
    this.activeLorePageId = null;
    await doc.save();
  }

  async deleteActiveLorebook() {
    const doc = this.activeLorebookDoc;
    if (!doc?.id || this.lorebookDocs.length <= 1) return;
    try {
      await dataApi.remove('lorebooks', doc.id);
    } catch (e) {
      console.error('[Terminal] Failed to delete lorebook file', e);
      return;
    }
    doc.dispose();
    this.writeIndex('lorebooks', this.readIndex('lorebooks').filter((x) => x !== doc.id));
    this.lorebookDocs = this.lorebookDocs.filter((d) => d !== doc);
    this.activeLorebookId = this.lorebookDocs[0].id!;
    this.activeLorePageId = null;
  }

  // --- Backup, restore and import ---

  /** Items with edits that are not safely on the server yet. */
  unsavedCount(): number {
    const docs: SyncedDoc<any>[] = [this.settingsDoc, ...this.characterDocs, ...this.lorebookDocs];
    return docs.filter((d) => d.status === 'dirty' || d.status === 'error' || d.status === 'conflict').length;
  }

  /** Everything in one object: what is on screen right now (including unsaved edits). Chat history is not included. */
  snapshotBackup(): AppBackup {
    return {
      format: BACKUP_FORMAT,
      version: 1,
      createdAt: new Date().toISOString(),
      settings: plain(this.settingsDoc.data),
      characters: this.characterDocs.map((d) => ({ id: d.id!, data: plain(d.data) })),
      lorebooks: this.lorebookDocs.map((d) => ({ id: d.id!, data: plain(d.data) }))
    };
  }

  private async addDoc<T extends object>(collection: 'characters' | 'lorebooks', id: string, data: T): Promise<SyncedDoc<T>> {
    const doc = new SyncedDoc<T>({ collection, id, defaults: plain(data) });
    await doc.load(); // not on the server yet, so it starts from the defaults (the imported data)
    this.writeIndex(collection, [...this.readIndex(collection), id]);
    await doc.save();
    return doc;
  }

  /** Replace an existing doc's contents and save. Returns false if the save did not go through. */
  private async overwriteDoc<T extends object>(doc: SyncedDoc<T>, data: T): Promise<boolean> {
    doc.data = plain(data);
    await doc.save();
    return doc.status === 'saved' || doc.status === 'clean';
  }

  /** Add a character card as a new file. The ID comes from the name and never collides with an existing one. */
  async importCharacter(card: CharacterCardV2): Promise<{ id: string; ok: boolean }> {
    const id = uniqueId(slugify(card.data.name, 'character'), new Set(this.characterDocs.map((d) => d.id!)));
    const doc = await this.addDoc('characters', id, card);
    this.characterDocs = [...this.characterDocs, doc];
    return { id, ok: doc.status === 'saved' };
  }

  async importLorebook(lorebook: Lorebook): Promise<{ id: string; ok: boolean }> {
    const id = uniqueId(slugify(lorebook.name, 'lorebook'), new Set(this.lorebookDocs.map((d) => d.id!)));
    const doc = await this.addDoc('lorebooks', id, lorebook);
    this.lorebookDocs = [...this.lorebookDocs, doc];
    return { id, ok: doc.status === 'saved' };
  }

  /**
   * Restore from a backup. Nothing is ever deleted.
   *   'add':       only items whose ID doesn't exist yet are created; everything else is left alone.
   *   'overwrite': items with a matching ID are replaced, and the global system prompt is restored.
   */
  async applyBackup(backup: AppBackup, mode: 'add' | 'overwrite'): Promise<RestoreResult> {
    const fresh = (): Counts => ({ added: 0, overwritten: 0, skipped: 0, failed: 0 });
    const result: RestoreResult = { characters: fresh(), lorebooks: fresh(), settings: 'untouched' };

    for (const item of backup.characters) {
      const existing = this.characterDocs.find((d) => d.id === item.id);
      if (!existing) {
        const doc = await this.addDoc('characters', item.id, item.data);
        this.characterDocs = [...this.characterDocs, doc];
        doc.status === 'saved' ? result.characters.added++ : result.characters.failed++;
      } else if (mode === 'overwrite') {
        (await this.overwriteDoc(existing, item.data)) ? result.characters.overwritten++ : result.characters.failed++;
      } else {
        result.characters.skipped++;
      }
    }

    for (const item of backup.lorebooks) {
      const existing = this.lorebookDocs.find((d) => d.id === item.id);
      if (!existing) {
        const doc = await this.addDoc('lorebooks', item.id, item.data);
        this.lorebookDocs = [...this.lorebookDocs, doc];
        doc.status === 'saved' ? result.lorebooks.added++ : result.lorebooks.failed++;
      } else if (mode === 'overwrite') {
        (await this.overwriteDoc(existing, item.data)) ? result.lorebooks.overwritten++ : result.lorebooks.failed++;
      } else {
        result.lorebooks.skipped++;
      }
    }

    if (mode === 'overwrite' && backup.settings) {
      result.settings = (await this.overwriteDoc(this.settingsDoc, backup.settings)) ? 'restored' : 'failed';
    }

    if (!this.characterDocs.some((d) => d.id === this.activeCharacterId)) this.activeCharacterId = this.characterDocs[0]?.id ?? '';
    if (!this.lorebookDocs.some((d) => d.id === this.activeLorebookId)) this.activeLorebookId = this.lorebookDocs[0]?.id ?? '';
    return result;
  }

  toggleConfig(force?: boolean) {
    this.isConfigOpen = force !== undefined ? force : !this.isConfigOpen;
    if (this.isConfigOpen) {
      this.isLorebookViewOpen = false;
    }
  }

  // Built fresh at send time from the current global prompt, character card and active lore.
  // It is never stored in `messages`, so Clear, edits and lore toggles can't leave it stale.
  //
  // Order: global prompt, character instructions, persona (description/personality/scenario),
  // example dialogue, lore, standing instructions. Empty fields are skipped.
  buildSystemPrompt(): string {
    const card = this.activeCharacterDoc?.data?.data;
    const name = card?.name || 'Assistant';
    const fill = (text?: string) => applyMacros(text, name, this.userName).trim();

    const parts: string[] = [this.settingsDoc.data.globalSystemPrompt];

    const cardRules = fill(card?.system_prompt);
    if (cardRules) parts.push(`=== CHARACTER INSTRUCTIONS ===\n${cardRules}`);

    const persona = [`Name: ${name}`, fill(card?.description)];
    const personality = fill(card?.personality);
    if (personality) persona.push(`Personality: ${personality}`);
    const scenario = fill(card?.scenario);
    if (scenario) persona.push(`Scenario: ${scenario}`);
    parts.push(`=== CHARACTER PERSONA ===\n${persona.filter(Boolean).join('\n')}`);

    const examples = fill(card?.mes_example).replace(/<START>/gi, '[Example conversation]');
    if (examples) parts.push(`=== EXAMPLE DIALOGUE ===\n${examples}`);

    if (this.activeSession?.lorebookEnabled) {
      const loreText = this.getAllActivePages()
        .filter((page) => page.content.trim())
        .map((page) => `### ${page.title}\n${page.content}`)
        .join('\n\n');
      if (loreText) {
        parts.push(`=== WORLD KNOWLEDGE BASE ===\nUse the following active lore entries to inform your responses, settings, and behaviors:\n\n${loreText}`);
      }
    }

    const standing = fill(card?.post_history_instructions);
    if (standing) parts.push(`=== STANDING INSTRUCTIONS ===\n${standing}`);

    return parts.join('\n\n');
  }

  /**
   * The greeting a new session starts with. The card picks from [first message, ...alternate greetings]:
   * the first message by default, a specific one, or a random one (card.extensions.greeting_pick).
   * Empty entries are never used; with nothing usable you get `fallback`.
   */
  private greeting(fallback: string): string {
    const card = this.activeCharacterDoc?.data?.data;
    const alternates = Array.isArray(card?.alternate_greetings) ? card.alternate_greetings : [];
    const pool = [card?.first_mes, ...alternates].map((g) =>
      applyMacros(typeof g === 'string' ? g : '', this.characterName, this.userName).trim()
    );
    const usable = pool.filter(Boolean);
    if (usable.length === 0) return fallback;

    const pick = card?.extensions?.greeting_pick;
    if (pick === 'random') return usable[Math.floor(Math.random() * usable.length)];
    const index = typeof pick === 'number' ? Math.floor(pick) : 0;
    return pool[index] || usable[0];
  }

  /** The picker's current value for a card: 'random', or an index (as text) into [first message, ...alternates]. */
  greetingChoice(doc: SyncedDoc<CharacterCardV2>): string {
    const pick = doc.data.data.extensions?.greeting_pick;
    if (pick === 'random') return 'random';
    const max = (doc.data.data.alternate_greetings ?? []).length;
    return String(Math.min(typeof pick === 'number' && pick > 0 ? Math.floor(pick) : 0, max));
  }

  // --- Models: the list comes from the gateway (/v1/models, which lists what LiteLLM serves) ---

  availableModels = $state<string[]>([]);
  modelsError = $state<string>('');

  /** The picker's options: the served models, plus the current one if it isn't in the list (so the picker always shows it). */
  get modelChoices(): string[] {
    const current = this.backendStatus.model;
    return !current || this.availableModels.includes(current) ? this.availableModels : [current, ...this.availableModels];
  }

  private modelsUrl(): string {
    return `${this.apiBaseUrl.replace(/\/+$/, '').replace(/\/chat\/completions$/, '')}/models`;
  }

  /** Fetch the served models. A failure keeps whatever is selected and what was listed before. */
  async loadModels() {
    try {
      const res = await fetch(this.modelsUrl(), { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = await res.json();
      const rows: unknown[] = Array.isArray(body?.data) ? body.data : [];
      // Embedding, speech and similar models can't chat, so they aren't offered.
      const ids = rows
        .map((m) => (m && typeof m === 'object' ? (m as { id?: unknown }).id : undefined))
        .filter((id): id is string => typeof id === 'string' && id.length > 0 && !NON_CHAT_MODEL.test(id));
      this.availableModels = [...new Set(ids)].sort((a, b) => a.localeCompare(b));
      this.modelsError = '';
      // The saved model was renamed or removed on the server: move to one that exists, rather than failing every message.
      if (this.availableModels.length > 0 && !this.availableModels.includes(this.backendStatus.model)) {
        this.setModel(this.availableModels[0]);
      }
    } catch (e) {
      this.modelsError = e instanceof Error ? e.message : 'unknown error';
    }
  }

  private setModel(id: string) {
    this.backendStatus.model = id;
    if (this.stateDoc.data.activeModel !== id) {
      this.updateState((s) => {
        s.activeModel = id;
      });
    }
  }

  /** Switch the model used for the next message (and on your other device). Not while a reply is streaming. */
  selectModel(id: string): boolean {
    if (this.isStreaming || !id || id === this.backendStatus.model) return false;
    this.setModel(id);
    return true;
  }

  // --- Profile pictures: stored inside the card, in extensions.avatar ---

  avatarOf(doc: SyncedDoc<CharacterCardV2>): string | null {
    return avatarFromExtensions(doc.data.data.extensions);
  }

  /** Set (or with null, remove) a card's picture. Like other edits, it is saved with the card's Save button. */
  setAvatar(doc: SyncedDoc<CharacterCardV2>, dataUrl: string | null) {
    const ext = { ...(doc.data.data.extensions ?? {}) };
    if (dataUrl) ext.avatar = dataUrl;
    else delete ext.avatar;
    doc.data.data.extensions = ext;
  }

  async setAvatarFromFile(doc: SyncedDoc<CharacterCardV2>, file: Blob) {
    this.setAvatar(doc, await blobToAvatar(file));
  }

  /** Make an image (one the AI made or looked at, for example) the active character's picture, and save it straight away. */
  async setActiveAvatarFromUrl(url: string): Promise<{ ok: boolean; message: string }> {
    const doc = this.activeCharacterDoc;
    if (!doc) return { ok: false, message: 'No character is selected.' };
    const name = doc.data.data.name || 'this character';
    try {
      this.setAvatar(doc, await urlToAvatar(url));
      await doc.save();
    } catch (e) {
      return { ok: false, message: e instanceof Error ? e.message : 'That image could not be used.' };
    }
    if (doc.status === 'saved' || doc.status === 'clean') return { ok: true, message: `Saved as ${name}'s profile picture.` };
    if (doc.status === 'conflict') return { ok: false, message: `${name}'s card was changed on another device. Open the character and choose which version to keep.` };
    return { ok: false, message: `The picture is set but didn't reach the server (${doc.error ?? doc.status}). Open the character and press Save.` };
  }

  setGreetingChoice(doc: SyncedDoc<CharacterCardV2>, value: string) {
    const ext = { ...(doc.data.data.extensions ?? {}) };
    if (value === 'random') ext.greeting_pick = 'random';
    else if (Number(value) > 0) ext.greeting_pick = Math.floor(Number(value));
    else delete ext.greeting_pick; // the first message is the default, so keep the card clean
    doc.data.data.extensions = ext;
  }

  private enforceContextLimits(targetLimit: number = this.contextLimit, ceilingLimit: number = this.contextLimit) {
    let currentTokens = this.estimatedTokens() + this.contextOverhead;
    if (currentTokens <= ceilingLimit) return;

    const systemMessages = this.messages.filter(m => m.role === 'system');
    let activeHistory = this.messages.filter(m => m.role !== 'system');

    while (currentTokens > targetLimit && activeHistory.length > 2) { // always keep the newest exchange
      const removed = activeHistory.shift();
      if (removed) {
        currentTokens -= (removed.tokens || Math.max(1, Math.round(removed.content.length / 3.8)));
      }
    }
    this.messages = [...systemMessages, ...activeHistory];
    this.sessionHistories.set(this.activeSessionId, [...this.messages]);
  }

  async hydrateSessionFromSQL(sessionId: string) {
    try {
      const res = await fetch(`/api/stm/sessions/${sessionId}/context`);
      let loadedMessages: Message[] = [];

      if (res.ok) {
        const data = await res.json();
        const sqlMessages = data.messages || [];
        if (sqlMessages.length > 0) {
          loadedMessages = sqlMessages.map((msg: any) => ({
            id: `msg-${msg.id || (Date.now() + Math.random().toString(36).substring(2))}`,
            dbId: typeof msg.id === 'number' ? msg.id : undefined,
            role: msg.role,
            content: msg.content,
            createdAt: msg.created_at ? new Date(msg.created_at) : new Date(),
            tokens: msg.token_count || Math.max(1, Math.round(msg.content.length / 3.8))
          }));
        }
      }

      if (loadedMessages.length === 0) {
        loadedMessages.push({ id: `msg-welcome-${Date.now()}`, role: 'assistant', content: this.greeting(`Session [${sessionId}] initialized. ${this.characterName} sovereign pipeline ready.`), createdAt: new Date(), tokens: 18 });
      }

      this.messages = loadedMessages;
      this.enforceContextLimits();
      this.sessionHistories.set(sessionId, [...this.messages]);
      this.syncActiveSessionMessageCount();
    } catch (err) {
      console.warn("[Terminal] Failed to hydrate SQL short-term memory:", err);
      this.messages = [{ id: `sys-error-${Date.now()}`, role: 'system', content: `[Gateway Error]: Failed to connect to SQL memory proxy.`, createdAt: new Date(), tokens: 10 }];
    }
  }

  selectSession(id: string) {
    if (this.isStreaming || id === this.activeSessionId) return;
    this.sessionHistories.set(this.activeSessionId, [...this.messages]);
    this.activeSessionId = id;

    const saved = this.sessionHistories.get(id);
    if (saved && saved.length > 0) {
      this.messages = [...saved];
    } else {
      this.messages = [{ id: `sess-${id}-${Date.now()}`, role: 'system', content: `Hydrating Session...`, createdAt: new Date(), tokens: 10 }];
      this.hydrateSessionFromSQL(id);
    }
    this.syncActiveSessionMessageCount();
  }

  createNewSession() {
    if (this.isStreaming) return;
    this.sessionHistories.set(this.activeSessionId, [...this.messages]);
    const newId = `session-${Date.now()}`;
    const greeting = this.greeting(`New session started. ${this.characterName} sovereign pipeline ready.`);

    const initialMessages: Message[] = [
      { id: `msg-welcome-${Date.now()}`, role: 'assistant', content: greeting, createdAt: new Date(), tokens: 18 }
    ];

    const title = friendlySessionTitle(newId, '');
    this.baseTitles.set(newId, title);
    const newSession: Session = { id: newId, title, updatedAt: new Date(), messageCount: initialMessages.length, lorebookEnabled: false };
    this.sessionHistories.set(newId, initialMessages);
    this.sessions = [newSession, ...this.sessions];
    this.activeSessionId = newId;
    this.messages = [...initialMessages];
    this.syncActiveSessionMessageCount();

    // Save it to the database straight away so it is still there after a reload. The import route
    // creates the session with a proper title, which the plain message route can't do.
    fetch(`/api/stm/sessions/${encodeURIComponent(newId)}/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_title: title,
        messages: [{ role: 'assistant', content: greeting, name: this.characterName, created_at: new Date().toISOString() }]
      })
    }).catch((e) => console.warn('[Terminal] could not save the new session yet:', e));
  }

  // --- Session list: comes from the short-term-memory service; custom titles and switches are shared state ---

  /** Load (or refresh) the session list. Keeps local-only sessions, and does nothing if the service is unreachable. */
  async loadSessions() {
    let rows: Array<{ id?: unknown; title?: unknown; created_at?: unknown }> | null = null;
    try {
      const res = await fetch('/api/stm/sessions', { cache: 'no-store' });
      if (res.ok) {
        const body = await res.json();
        if (Array.isArray(body)) rows = body;
      }
    } catch {
      /* offline: keep the sessions we already have */
    }
    if (!rows) return;

    const existing = new Map(this.sessions.map((s) => [s.id, s]));
    const seen = new Set<string>();
    const fromServer: Session[] = [];
    for (const row of rows) {
      const id = row?.id;
      if (typeof id !== 'string' || !id || seen.has(id)) continue;
      seen.add(id);
      const createdAt = typeof row.created_at === 'string' ? row.created_at : null;
      this.baseTitles.set(id, this.seedTitles.get(id) ?? friendlySessionTitle(id, typeof row.title === 'string' ? row.title : '', createdAt));
      const prev = existing.get(id);
      const created = createdAt ? new Date(createdAt) : null;
      fromServer.push({
        id,
        title: '', // filled in by applySharedSessionState
        updatedAt: prev?.updatedAt ?? (created && !isNaN(created.getTime()) ? created : new Date()),
        messageCount: prev?.messageCount ?? 0,
        lorebookEnabled: prev?.lorebookEnabled ?? false
      });
    }

    // Sessions the server doesn't know about: ones just created here, and the two built-in ones if they aren't in the database.
    const localOnly = this.sessions.filter((s) => !seen.has(s.id));
    let list: Session[] = [
      ...localOnly.filter((s) => !this.seedTitles.has(s.id)),
      ...fromServer,
      ...localOnly.filter((s) => this.seedTitles.has(s.id))
    ];

    // The default page is always listed, even before it has any messages.
    if (!list.some((s) => s.id === DEFAULT_SESSION_ID)) {
      this.baseTitles.set(DEFAULT_SESSION_ID, friendlySessionTitle(DEFAULT_SESSION_ID, ''));
      list = [{ id: DEFAULT_SESSION_ID, title: '', updatedAt: new Date(), messageCount: 0, lorebookEnabled: false }, ...list];
    }

    // First time the default page has its own entry: it keeps the lorebook switch it was showing before.
    if (!(DEFAULT_SESSION_ID in (this.stateDoc.data.sessionLorebook ?? {}))) {
      const source = this.defaultFlagSource;
      const inherited = this.stateDoc.data.sessionLorebook?.[source.id] ?? source.enabled;
      this.updateState((s) => {
        s.sessionLorebook = { ...(s.sessionLorebook ?? {}) };
        if (!(DEFAULT_SESSION_ID in s.sessionLorebook)) s.sessionLorebook[DEFAULT_SESSION_ID] = inherited;
      });
    }

    this.sessions = list;
    this.applySharedSessionState();
  }

  /** Give a session a custom name. An empty name (or the original one) removes the custom name. */
  renameSession(id: string, title: string) {
    const session = this.sessions.find((s) => s.id === id);
    if (!session) return;
    const clean = title.trim().slice(0, 80);
    const base = this.baseTitles.get(id) ?? '';
    this.updateState((s) => {
      s.sessionTitles = { ...(s.sessionTitles ?? {}) };
      if (clean && clean !== base) s.sessionTitles[id] = clean;
      else delete s.sessionTitles[id];
    });
    this.applySharedSessionState();
  }

  /** Delete a session and all of its messages from the database. Returns false if it could not be deleted. */
  async deleteSession(id: string): Promise<boolean> {
    if (this.isStreaming) return false;
    try {
      const res = await fetch(`/api/stm/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' });
      if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
    } catch (e) {
      console.error('[Terminal] failed to delete session', e);
      return false;
    }

    const wasActive = this.activeSessionId === id;
    this.sessionHistories.delete(id);
    this.baseTitles.delete(id);
    this.sessions = this.sessions.filter((s) => s.id !== id);
    this.updateState((s) => {
      if (s.sessionLorebook) delete s.sessionLorebook[id];
      if (s.sessionTitles) delete s.sessionTitles[id];
    });

    // The default page always exists: deleting it leaves a fresh, empty one.
    if (id === DEFAULT_SESSION_ID) {
      this.baseTitles.set(DEFAULT_SESSION_ID, friendlySessionTitle(DEFAULT_SESSION_ID, ''));
      this.sessions = [{ id: DEFAULT_SESSION_ID, title: '', updatedAt: new Date(), messageCount: 0, lorebookEnabled: false }, ...this.sessions];
    }
    this.applySharedSessionState();

    if (wasActive) {
      const next = this.sessions.find((s) => s.id === DEFAULT_SESSION_ID) ?? this.sessions[0];
      this.activeSessionId = next.id;
      const saved = this.sessionHistories.get(next.id);
      if (saved && saved.length > 0) {
        this.messages = [...saved];
      } else {
        this.messages = [{ id: `sess-${next.id}-${Date.now()}`, role: 'system', content: 'Hydrating Session...', createdAt: new Date(), tokens: 10 }];
        await this.hydrateSessionFromSQL(next.id);
      }
      this.syncActiveSessionMessageCount();
    }
    return true;
  }

  clearScreen() {
    this.messages = [];
    this.sessionHistories.set(this.activeSessionId, []);
    this.syncActiveSessionMessageCount();
  }

  addCheckpoint() {
    const cpMsg: Message = { id: `cp-${Date.now()}`, role: 'system', content: `[CHECKPOINT REACHED]: Current context length ~${this.totalTokenCount} tokens.`, createdAt: new Date(), tokens: 22 };
    this.messages.push(cpMsg);
    this.sessionHistories.set(this.activeSessionId, [...this.messages]);
    this.syncActiveSessionMessageCount();
  }

  toggleWorkspace(force?: boolean) {
    this.isWorkspaceOpen = force !== undefined ? force : !this.isWorkspaceOpen;
    if (this.isWorkspaceOpen) this.isLorebookViewOpen = false;
  }

  selectWorkspaceItem(item: WorkspaceItem) {
    this.activeWorkspaceItem = item;
    this.isWorkspaceOpen = true;
  }

  abortGeneration() {
    if (this.abortController) { this.abortController.abort(); this.abortController = null; }
    this.isStreaming = false;
    const lastMsg = this.messages[this.messages.length - 1];
    if (lastMsg && lastMsg.role === 'assistant') {
      lastMsg.isStreaming = false;
      if (!lastMsg.content.includes('[Generation halted]')) lastMsg.content += ' [Generation halted by user]';
    }
    this.sessionHistories.set(this.activeSessionId, [...this.messages]);
    this.syncActiveSessionMessageCount();
  }

  /**
   * Save a pasted/dropped/attached image into this character's workspace folder (<character>/uploads/) and get it ready to send.
   * Big photos are shrunk first. Throws an Error with a readable message if the upload fails.
   */
  async uploadImage(file: Blob, fileName = 'image'): Promise<ChatAttachment> {
    const blob = await prepareImage(file);
    const folder = `${slugify(this.characterName, 'character')}/uploads`;
    let res: Response;
    try {
      res = await fetch(`/v1/workspace/upload?folder=${encodeURIComponent(folder)}`, {
        method: 'POST',
        headers: { 'Content-Type': blob.type || 'application/octet-stream' },
        body: blob
      });
    } catch {
      throw new Error("Couldn't reach the server to save the image. Check your connection and try again.");
    }
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { detail = (await res.json()).error || detail; } catch { /* keep the status */ }
      throw new Error(`Couldn't save the image: ${detail}`);
    }
    const saved = await res.json();
    const item = { rel: String(saved.rel ?? ''), path: String(saved.path ?? ''), url: String(saved.url ?? ''), name: String(saved.name ?? fileName) };
    if (!item.path || !item.url) throw new Error("The server's answer to the upload was incomplete. Is the proxy up to date?");
    return { path: item.path, rel: item.rel, url: item.url, dataUrl: await blobToDataUrl(blob), name: item.name };
  }

  async sendMessage(prompt: string, attachments: ChatAttachment[] = []): Promise<void> {
    const trimmed = composeWithAttachments(prompt, attachments);
    if (!trimmed || this.isStreaming) return;

    // Anything pasted shows up in the workspace panel right away, the same as an image the AI opens.
    for (const a of [...attachments].reverse()) {
      this.addWorkspaceItem({
        id: `upload-${Date.now()}-${a.name}`,
        type: 'image',
        title: a.name,
        filepath: a.rel,
        url: a.url,
        source: 'viewed',
        timestamp: new Date()
      });
    }

    // An image rides inline for the turn it was sent (and regenerations of that reply). After that only its saved path stays
    // in the text, so it stops using context; the AI can open it again with its view tool, which costs the tokens for one turn only.
    for (const old of this.messages) {
      if (old.inlineImages?.length) {
        old.tokens = Math.max(1, (old.tokens || 0) - old.inlineImages.length * IMAGE_TOKEN_ESTIMATE);
        delete old.inlineImages;
      }
    }

    this.messages.push({
      id: `user-${Date.now()}`, role: 'user', content: trimmed, createdAt: new Date(),
      tokens: Math.max(1, Math.round(trimmed.length / 3.8)) + attachments.length * IMAGE_TOKEN_ESTIMATE,
      ...(attachments.length ? { inlineImages: attachments.map((a) => a.dataUrl) } : {})
    });
    const userMsg = this.messages[this.messages.length - 1];
    const asstMsgId = `asst-${Date.now()}`;
    const model = this.backendStatus.model;
    this.messages.push({ id: asstMsgId, role: 'assistant', content: '', thinking: '', createdAt: new Date(), tokens: 0, isStreaming: true, model });
    
    const asstMsg = this.messages[this.messages.length - 1];
    this.enforceContextLimits();
    this.syncActiveSessionMessageCount();
    this.isStreaming = true;
    this.abortController = new AbortController();

    fetch(`/api/stm/sessions/${this.activeSessionId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role: 'user', content: trimmed, ...(this.userNameSetting ? { name: this.userNameSetting } : {}) })
    }).then((res) => this.rememberDbId(userMsg, res)).catch(e => console.warn(e));

    await this.streamReply(asstMsg, model, trimmed);
  }

  /**
   * Stream the model's reply into `asstMsg` and save it to the database.
   * The caller has already put `asstMsg` on screen with isStreaming set, and set isStreaming and abortController.
   * `simulatePrompt` is only for the offline demo fallback; pass null to never fake a reply (regenerate).
   * Returns how it ended: 'ok', 'error' (the gateway answered with an error or couldn't be reached), or 'aborted' (Stop).
   */
  private async streamReply(asstMsg: Message, model: string, simulatePrompt: string | null): Promise<'ok' | 'error' | 'aborted'> {
    try {
      const payloadMessages = [
        { role: 'system', content: resolveTimeMacros(this.systemPrompt) },
        ...this.messages
          .filter((m) => m.id !== asstMsg.id && m.role !== 'system')
          .map((m) => {
            const content = toModelContent(m.content, m.inlineImages);
            return { role: m.role, content: STAMP_USER_MESSAGES && m.role === 'user' ? stampContent(content, m.createdAt) : content };
          })
      ];
      const base = this.apiBaseUrl.replace(/\/+$/, '');
      const endpoint = base.endsWith('/chat/completions') ? base : `${base}/chat/completions`;

      // Our count of what is being sent, to compare with the server's exact count when the reply ends.
      const sentEstimate = this.estimatedTokens() - (asstMsg.tokens || 0);
      const workspaceEventsBefore = this.workspaceEventCount;
      let sawPromptUsage = false;
      let cutOff = false;

      const res = await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' }, body: JSON.stringify({ model, stream: true, stream_options: { include_usage: true }, character: this.characterName, messages: payloadMessages }), signal: this.abortController.signal });
      if (!res.ok) {
        asstMsg.content = `[Gateway Error: HTTP ${res.status}] The model "${model}" didn't answer. Its server may not be running. Pick another model from the list at the top, or start this one.`;
        asstMsg.isStreaming = false;
        return 'error';
      }

      const reader = res.body!.getReader();
      const decoder = new TextDecoder('utf-8');
      let lineBuf = '';
      let tagAcc = '';
      let isThinking = false;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        lineBuf += decoder.decode(value, { stream: true });
        const lines = lineBuf.split('\n');
        lineBuf = lines.pop() ?? '';

        for (const rawLine of lines) {
          const line = rawLine.trim();
          if (!line || line.startsWith(':')) continue;
          if (line === 'data: [DONE]') { asstMsg.isStreaming = false; break; }
          if (line.startsWith('data: ')) {
            const dataStr = line.slice(6);
            try {
              const parsed = JSON.parse(dataStr);
              if (parsed.type === 'prompt_usage' && typeof parsed.prompt_tokens === 'number') {
                sawPromptUsage = true;
                this.lastPromptTokens = parsed.prompt_tokens;
                this.calibrate(parsed.prompt_tokens, sentEstimate);
              }
              else if (parsed.type) { this.handleCustomStreamChunk(parsed as               StreamChunk, asstMsg); }
              else if (parsed.usage && typeof parsed.usage.prompt_tokens === 'number') {
                // Exact size of the request, reported once at the end of the reply.
                this.lastPromptTokens = parsed.usage.prompt_tokens;
                if (typeof parsed.usage.completion_tokens === 'number' && parsed.usage.completion_tokens > 0) this.recordCompletion(parsed.usage.completion_tokens, cutOff);
                // A turn where the AI opened images or files had extra, temporary content in this (second) request, so
                // it isn't a fair measure of what the saved history costs. On those turns the proxy sends the exact
                // size of the first request instead (prompt_usage); on all other turns this report is the one.
                if (this.workspaceEventCount === workspaceEventsBefore && !sawPromptUsage) this.calibrate(parsed.usage.prompt_tokens, sentEstimate);
              }
              else {
                if (parsed.choices?.[0]?.finish_reason === 'length') cutOff = true;
                const deltaObj = parsed.choices?.[0]?.delta;
                if (deltaObj?.reasoning_content) asstMsg.thinking = (asstMsg.thinking || '') + deltaObj.reasoning_content;
                if (deltaObj?.content) tagAcc = this.processStreamAccumulator(tagAcc + deltaObj.content, asstMsg, isThinking, (st) => { isThinking = st; });
              }
            } catch {
              tagAcc = this.processStreamAccumulator(tagAcc + dataStr, asstMsg, isThinking, (st) => { isThinking = st; });
            }
          }
        }
      }
      if (tagAcc) {
        if (isThinking) asstMsg.thinking = (asstMsg.thinking || '') + tagAcc;
        else { asstMsg.content += tagAcc; asstMsg.tokens += Math.max(1, Math.round(tagAcc.length / 3.8)); }
      }
      if (asstMsg.content) {
         fetch(`/api/stm/sessions/${this.activeSessionId}/messages`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ role: 'assistant', content: asstMsg.content, name: this.characterName }) }).then((res) => this.rememberDbId(asstMsg, res)).catch(e => console.warn(e));
      }
      return 'ok';
    } catch (err: any) {
      if (err?.name === 'AbortError') return 'aborted';
      if (simulatePrompt !== null && this.allowSimulationFallback) {
        asstMsg.content = '';
        await this.runLocalSimulation(simulatePrompt, asstMsg);
        return 'ok';
      }
      return 'error';
    } finally {
      this.isStreaming = false; 
      asstMsg.isStreaming = false; 
      this.abortController = null;
      this.syncActiveSessionMessageCount();
      this.sessionHistories.set(this.activeSessionId, [...this.messages]);
    }
  }

  private processStreamAccumulator(acc: string, msg: Message, isThinking: boolean, setThinking: (val: boolean) => void): string {
    let current = acc;
    while (current.length > 0) {
      if (!isThinking) {
        const tagIdx = current.indexOf('<think>');
        if (tagIdx !== -1) {
          if (tagIdx > 0) {
            const text = current.slice(0, tagIdx);
            msg.content += text;
            msg.tokens += Math.max(1, Math.round(text.length / 3.8));
          }
          setThinking(true);
          isThinking = true;
          current = current.slice(tagIdx + 7);
          continue;
        }

        const partialMatches = ['<', '<t', '<th', '<thi', '<thin', '<think'];
        const matchedPartial = partialMatches.find((p) => current.endsWith(p));
        if (matchedPartial) {
          const safeLength = current.length - matchedPartial.length;
          if (safeLength > 0) {
            const text = current.slice(0, safeLength);
            msg.content += text;
            msg.tokens += Math.max(1, Math.round(text.length / 3.8));
            return matchedPartial;
          }
          return current;
        }

        msg.content += current;
        msg.tokens += Math.max(1, Math.round(current.length / 3.8));
        return '';
      } else {
        const tagIdx = current.indexOf('</think>');
        if (tagIdx !== -1) {
          if (tagIdx > 0) msg.thinking = (msg.thinking || '') + current.slice(0, tagIdx);
          setThinking(false);
          isThinking = false;
          current = current.slice(tagIdx + 8);
          continue;
        }

        const partialMatches = ['<', '</', '</t', '</th', '</thi', '</thin', '</think'];
        const matchedPartial = partialMatches.find((p) => current.endsWith(p));
        if (matchedPartial) {
          const safeLength = current.length - matchedPartial.length;
          if (safeLength > 0) {
            msg.thinking = (msg.thinking || '') + current.slice(0, safeLength);
            return matchedPartial;
          }
          return current;
        }

        msg.thinking = (msg.thinking || '') + current;
        return '';
      }
    }
    return '';
  }

  // --- Editing and deleting messages (each message is a row in the short-term-memory database) ---

  /** The save route answers {"id": <row id>}. Keep it on the message so it can be edited or deleted later. */
  private async rememberDbId(msg: Message, res: Response) {
    if (!res.ok) return;
    const body = await res.json().catch(() => null);
    if (typeof body?.id === 'number') msg.dbId = body.id;
  }

  /**
   * The database row for a message: its known id, or one found by matching the saved history, or null if it was never
   * saved (the default page's greeting, a halted reply). undefined means the database couldn't be checked.
   */
  private async findDbId(msg: Message): Promise<number | null | undefined> {
    if (typeof msg.dbId === 'number') return msg.dbId;
    let rows: Array<{ id?: unknown; role?: unknown; content?: unknown }>;
    try {
      const res = await fetch(`/api/stm/sessions/${encodeURIComponent(this.activeSessionId)}/messages`, { cache: 'no-store' });
      if (res.status === 404) return null; // the session isn't in the database at all
      if (!res.ok) return undefined;
      const body = await res.json();
      rows = Array.isArray(body) ? body : [];
    } catch {
      return undefined;
    }
    // Match by role and text. What is on screen is the END of the saved history, so identical messages are counted from the end.
    const same = (role: string, content: string) => role === msg.role && content.trim() === msg.content.trim();
    const onScreen = this.messages.filter((m) => m.role !== 'system' && same(m.role, m.content));
    const fromEnd = onScreen.length - 1 - onScreen.findIndex((m) => m.id === msg.id);
    const saved = rows.filter((r) => typeof r.id === 'number' && same(String(r.role), String(r.content ?? '')));
    const hit = fromEnd >= 0 && fromEnd < saved.length ? saved[saved.length - 1 - fromEnd] : undefined;
    if (!hit) return null;
    msg.dbId = hit.id as number;
    return msg.dbId;
  }

  /** Change a message's text, in the database and on screen. */
  async editMessage(id: string, newContent: string): Promise<{ ok: boolean; message: string }> {
    if (this.isStreaming) return { ok: false, message: 'Wait for the reply to finish first.' };
    const msg = this.messages.find((m) => m.id === id);
    if (!msg || msg.role === 'system') return { ok: false, message: "That message can't be edited." };
    const content = newContent.replace(/\s+$/, '');
    if (!content.trim()) return { ok: false, message: "A message can't be empty. Use delete to remove it." };
    if (content === msg.content) return { ok: true, message: 'No changes.' };

    const dbId = await this.findDbId(msg);
    if (dbId === undefined) return { ok: false, message: "Couldn't reach the database, so nothing was changed. Try again." };
    if (typeof dbId === 'number') {
      try {
        const res = await fetch(`/api/stm/messages/${dbId}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content })
        });
        if (res.status === 404) return { ok: false, message: 'That message is no longer in the database (it may have been deleted on another device).' };
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
      } catch (e) {
        return { ok: false, message: `Couldn't save the edit (${e instanceof Error ? e.message : 'network error'}). Nothing was changed.` };
      }
    }

    const live = this.messages.find((m) => m.id === id);
    if (!live) return { ok: false, message: 'That message is gone.' };
    live.content = content;
    live.tokens = Math.max(1, Math.round(content.length / 3.8));
    this.sessionHistories.set(this.activeSessionId, [...this.messages]);
    return {
      ok: true,
      message: dbId === null ? 'Edited on screen only: this message was never saved to the database.' : 'Saved.'
    };
  }

  /** Only the last message, and only if it is an AI reply to something you said. */
  canRegenerate(msg: Message): boolean {
    if (this.isStreaming || msg.role !== 'assistant' || msg.isStreaming) return false;
    const last = this.messages[this.messages.length - 1];
    if (!last || last.id !== msg.id) return false;
    const before = this.messages.slice(0, -1).filter((m) => m.role !== 'system').at(-1);
    return before?.role === 'user';
  }

  /**
   * Ask the model for a new version of the last reply, using the model selected now. The old reply is held aside while
   * the new one streams: if the new one fails, is stopped, or comes back empty, the old one is put back untouched.
   * Once the new reply exists, the old one is removed from the database.
   */
  async regenerateLast(): Promise<{ ok: boolean; message: string }> {
    const old = this.messages[this.messages.length - 1];
    if (!old || !this.canRegenerate(old)) return { ok: false, message: 'Only the last AI reply can be regenerated.' };

    // Find the old reply's database row first, so it can be removed once the new reply is saved.
    const oldDbId = await this.findDbId(old);
    if (oldDbId === undefined) return { ok: false, message: "Couldn't reach the database, so nothing was changed. Try again." };
    if (this.isStreaming || this.messages[this.messages.length - 1]?.id !== old.id) {
      return { ok: false, message: 'The conversation changed. Try again.' };
    }

    const model = this.backendStatus.model;
    const held = this.messages.splice(this.messages.length - 1, 1)[0];
    const newId = `asst-${Date.now()}`;
    this.messages.push({ id: newId, role: 'assistant', content: '', thinking: '', createdAt: new Date(), tokens: 0, isStreaming: true, model });
    const fresh = this.messages[this.messages.length - 1];
    this.enforceContextLimits();
    this.isStreaming = true;
    this.abortController = new AbortController();
    this.syncActiveSessionMessageCount();

    const status = await this.streamReply(fresh, model, null);

    if (status !== 'ok' || !fresh.content.trim()) {
      // Put the old reply back exactly as it was.
      const detail = fresh.content.startsWith('[Gateway Error') ? fresh.content : '';
      this.messages = this.messages.filter((m) => m.id !== newId);
      this.messages.push(held);
      this.sessionHistories.set(this.activeSessionId, [...this.messages]);
      this.syncActiveSessionMessageCount();
      const why = status === 'aborted' ? 'Stopped.' : detail || (status === 'error' ? "Couldn't reach the model." : 'The model sent back an empty reply.');
      return { ok: false, message: `${why} The previous reply was kept.` };
    }

    // The new reply is on screen (and being saved). Now drop the old one from the database.
    if (typeof oldDbId === 'number') {
      try {
        const res = await fetch(`/api/stm/messages/${oldDbId}`, { method: 'DELETE' });
        if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
      } catch {
        return { ok: true, message: "The new reply is here, but the old one couldn't be removed from the database. It will show again after a reload." };
      }
    }
    return { ok: true, message: 'Regenerated.' };
  }

  /** Remove a message, from the database and from the screen. */
  async deleteMessage(id: string): Promise<{ ok: boolean; message: string }> {
    if (this.isStreaming) return { ok: false, message: 'Wait for the reply to finish first.' };
    const msg = this.messages.find((m) => m.id === id);
    if (!msg || msg.role === 'system') return { ok: false, message: "That message can't be deleted." };

    const dbId = await this.findDbId(msg);
    if (dbId === undefined) return { ok: false, message: "Couldn't reach the database, so nothing was deleted. Try again." };
    if (typeof dbId === 'number') {
      try {
        const res = await fetch(`/api/stm/messages/${dbId}`, { method: 'DELETE' });
        if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`); // 404: already gone, which is what we wanted
      } catch (e) {
        return { ok: false, message: `Couldn't delete it (${e instanceof Error ? e.message : 'network error'}). Nothing was changed.` };
      }
    }

    this.messages = this.messages.filter((m) => m.id !== id);
    this.sessionHistories.set(this.activeSessionId, [...this.messages]);
    this.syncActiveSessionMessageCount();
    return {
      ok: true,
      message: dbId === null ? 'Removed from the screen. It was never saved to the database.' : 'Deleted.'
    };
  }

  setAutoOpenViewed(on: boolean) {
    this.autoOpenViewed = on;
    try {
      localStorage.setItem('workspace_auto_open_viewed', String(on));
    } catch {
      /* storage blocked: the choice still applies until reload */
    }
  }

  /**
   * Add an item to the Inspector and make it the active one (the newest viewed item is "what the AI is looking at now").
   * An item for a file that is already listed replaces the old entry instead of piling up duplicates.
   * Created items always open the panel; viewed items open it only if auto-open is on.
   */
  addWorkspaceItem(item: WorkspaceItem) {
    const keyOf = (i: WorkspaceItem) => (i.filepath ? i.filepath.replace(/\\/g, '/').replace(/^\.?\//, '') : (i.url ?? ''));
    const key = keyOf(item);
    const others = key ? this.workspaceItems.filter((i) => keyOf(i) !== key) : this.workspaceItems;
    this.workspaceItems = [item, ...others].slice(0, 30);
    this.activeWorkspaceItem = item;
    if (item.source !== 'viewed' || this.autoOpenViewed) this.isWorkspaceOpen = true;
  }

  private handleCustomStreamChunk(chunk: StreamChunk, msg: Message) {
    switch (chunk.type) {
      case 'text_delta':
        msg.content += chunk.delta;
        msg.tokens += Math.max(1, Math.round(chunk.delta.length / 3.8));
        break;
      case 'thought_delta':
        msg.thinking = (msg.thinking || '') + chunk.delta;
        break;
      case 'token_count':
        msg.tokens = chunk.tokens;
        break;
      case 'workspace_item': {
        this.workspaceEventCount++;
        // The gateway's item can be incomplete or shaped differently than expected; fill it in so the panel can't break on it.
        this.addWorkspaceItem(normalizeWorkspaceItem(chunk.item));
        break;
      }
      case 'finish':
        msg.isStreaming = false;
        break;
    }
  }

  // Shown only when the gateway can't be reached, so the interface can still be tried out. It says so plainly.
  private async runLocalSimulation(prompt: string, targetMsg: Message) {
    targetMsg.thinking = (targetMsg.thinking || '') + 'The gateway could not be reached, so this is a placeholder reply.';
    await new Promise((r) => setTimeout(r, 60));
    const reply = `[Demo mode] The gateway could not be reached, so this is a placeholder reply, not a model answer.\n\nYou said: "${prompt}"`;
    for (let i = 0; i < reply.length; i += 3) {
      if (!this.isStreaming) return;
      targetMsg.content += reply.slice(i, i + 3);
      targetMsg.tokens += 1;
      await new Promise((r) => setTimeout(r, 20));
    }
  }
}
