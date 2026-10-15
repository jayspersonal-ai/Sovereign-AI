// src/lib/storage-migrate.ts
//
// Older versions kept their browser settings under keys that began with "lyra:" or "lyra_". The prefix is gone now.
// This copies each old key to its new name once (never overwriting a newer value) and removes the old one, so unsaved
// drafts, the selected character and the other small settings carry over.

export function migrateStorageKeys(): void {
  try {
    const old: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key && /^lyra[:_]/.test(key)) old.push(key);
    }
    for (const key of old) {
      const next = key.replace(/^lyra[:_]/, '');
      const value = localStorage.getItem(key);
      if (value !== null && localStorage.getItem(next) === null) localStorage.setItem(next, value);
      localStorage.removeItem(key);
    }
  } catch {
    /* storage unavailable: nothing to carry over */
  }
}
