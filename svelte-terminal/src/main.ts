import './app.css';
import { mount } from 'svelte';
import { migrateStorageKeys } from './lib/storage-migrate';

migrateStorageKeys(); // before anything reads the browser's saved settings

// Diagnostic: if the app crashes, show the error on screen (tap it to dismiss) instead of a blank page.
// Handy on a phone, where there's no dev console. Safe to leave in or to revert to the original main.ts.
function showFatal(label: string, err: unknown) {
  const msg = err instanceof Error ? `${err.message}\n\n${err.stack ?? ''}` : String(err);
  const el = document.createElement('pre');
  el.style.cssText =
    'position:fixed;inset:0;z-index:99999;margin:0;padding:16px;overflow:auto;background:#1c0a0a;color:#fca5a5;font:12px/1.5 monospace;white-space:pre-wrap';
  el.textContent = `${label}  (tap to dismiss)\n\n${msg}`;
  el.onclick = () => el.remove();
  document.body.appendChild(el);
}
window.addEventListener('error', (e) => showFatal('Uncaught error', e.error ?? e.message));
window.addEventListener('unhandledrejection', (e) => showFatal('Unhandled promise rejection', e.reason));

// Spell check and autocorrect for every text area (chat box, message editor, character and lorebook fields).
// Desktop browsers do this on their own; phone keyboards usually need the attributes spelled out.
function enableSpelling(el: Element | null) {
  if (!(el instanceof HTMLTextAreaElement)) return;
  if (!el.hasAttribute('spellcheck')) el.setAttribute('spellcheck', 'true');
  if (!el.hasAttribute('autocorrect')) el.setAttribute('autocorrect', 'on');
  if (!el.hasAttribute('autocapitalize')) el.setAttribute('autocapitalize', 'sentences');
  if (!el.lang) el.lang = document.documentElement.lang || 'en';
}
document.addEventListener('focusin', (e) => enableSpelling(e.target as Element), true);
new MutationObserver((records) => {
  for (const r of records) r.addedNodes.forEach((n) => {
    if (n instanceof Element) {
      enableSpelling(n);
      n.querySelectorAll('textarea').forEach(enableSpelling);
    }
  });
}).observe(document.documentElement, { childList: true, subtree: true });

const target = document.getElementById('app') ?? document.body;
let app: ReturnType<typeof mount> | undefined;

try {
  // Dynamic import so a failure while loading App.svelte or its imports is reported too.
  const { default: App } = await import('./App.svelte');
  app = mount(App, { target });
} catch (err) {
  showFatal('App failed to start', err);
}

export default app;
