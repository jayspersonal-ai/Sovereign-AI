// src/lib/clipboard.ts
//
// Copy text to the clipboard.
//
// Browsers only provide navigator.clipboard on secure pages (https, or localhost). This app is often opened over plain
// http (for example through Tailscale), where that API simply doesn't exist, so a copy button that relies on it silently
// does nothing. This helper uses it when it's there and otherwise falls back to the older select-and-copy method,
// which works everywhere.

/** The older way: put the text in a hidden box, select it, and run the browser's copy command. */
function legacyCopy(text: string): boolean {
  const previouslyFocused = document.activeElement as HTMLElement | null;
  const box = document.createElement('textarea');
  box.value = text;
  box.setAttribute('readonly', ''); // keeps the on-screen keyboard from opening on phones
  // Off to the side and invisible; 16px stops iOS from zooming in when the box is focused.
  box.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;padding:0;border:0;opacity:0;font-size:16px;';
  document.body.appendChild(box);
  let ok = false;
  try {
    box.focus();
    box.select();
    box.setSelectionRange(0, text.length); // iOS needs the range set explicitly
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  } finally {
    box.remove();
    previouslyFocused?.focus?.();
  }
  return ok;
}

/**
 * Copy `text`. Returns true if it was copied. Call it straight from a click handler: the fallback only works
 * while the browser still counts the tap as a user action, so nothing should be awaited before calling it.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  if (typeof text !== 'string' || text.length === 0) return false;
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText && typeof window !== 'undefined' && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      /* permission refused or blocked: try the older method */
    }
  }
  return typeof document !== 'undefined' ? legacyCopy(text) : false;
}
