export type MessageRole = 'user' | 'assistant' | 'system';

export interface Message {
  id: string;
  role: MessageRole;
  content: string;
  thinking?: string;
  createdAt: Date;
  tokens: number;
  isStreaming?: boolean;
  model?: string; // which model wrote an AI message (known for messages from this visit)
  inlineImages?: string[]; // images sent with this message this visit (not saved; later turns use the saved path in the text)
  dbId?: number; // the message's row in the short-term-memory database, once known (needed to edit or delete it)
}

export interface Session {
  id: string;
  title: string;
  updatedAt: Date;
  messageCount: number;
  lorebookEnabled?: boolean; // NEW: Binary on/off flag for this session
}

export type WorkspaceItemType = 'image' | 'text' | 'json';

export interface WorkspaceItem {
  id: string;
  type: WorkspaceItemType;
  title: string;
  filepath?: string;
  url?: string;
  content?: string;
  timestamp: Date;
  source?: 'viewed' | 'created'; // viewed: the AI was shown it; created: the AI made it
}

export type StreamChunk =
  | { type: 'text_delta'; delta: string }
  | { type: 'thought_delta'; delta: string }
  | { type: 'workspace_item'; item: WorkspaceItem }
  | { type: 'token_count'; tokens: number }
  | { type: 'prompt_usage'; prompt_tokens: number } // exact size of the request before tool results (sent by the proxy on tool turns)
  | { type: 'finish'; reason?: string };

export interface BackendStatus {
  online: boolean;
  model: string;
}

// Character Card V2 ("chara_card_v2"), the format SillyTavern and most other frontends use.
// Unknown/extra fields in a card file are preserved when you save.
export interface CharacterCardData {
  name: string;
  description: string; // the persona text this app sends to the model
  personality: string;
  scenario: string;
  first_mes: string; // greeting shown at the start of a new session
  mes_example: string;
  creator_notes: string;
  system_prompt: string;
  post_history_instructions: string;
  alternate_greetings: string[];
  tags: string[];
  creator: string;
  character_version: string;
  extensions: Record<string, unknown>;
}

export interface CharacterCardV2 {
  spec: 'chara_card_v2';
  spec_version: '2.0';
  data: CharacterCardData;
}

export interface SettingsData {
  globalSystemPrompt: string;
  userName?: string; // fills {{user}} in cards; empty means "User"
  userAvatar?: string; // your own profile picture, shown on your messages (a small image data URL)
}

// One lorebook = one file: a name plus the folder/page tree.
export interface Lorebook {
  name: string;
  nodes: LoreNode[];
}

// NEW: Hierarchical Lorebook Tree Structures
export interface LorePage {
  id: string;
  type: 'page';
  title: string;
  content: string;
  isActive: boolean;
}

export interface LoreFolder {
  id: string;
  type: 'folder';
  title: string;
  children: (LoreFolder | LorePage)[];
  isExpanded?: boolean; // legacy: no longer saved (folder state is per-device)
}

export type LoreNode = LoreFolder | LorePage;

/** An image attached to the message being written: already saved in the AI's workspace folder. */
export interface ChatAttachment {
  path: string; // where it was saved (the AI's tools can open this)
  rel: string; // the same place, relative to the workspace folder
  url: string; // address for showing it in the browser
  dataUrl: string; // the image itself, for sending inline to the model
  name: string;
}
