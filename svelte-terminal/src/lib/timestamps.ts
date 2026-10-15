// Gives the model a real clock. Models can't tell what time it is, so they guess (and guess badly).
// Two things are added to what is SENT to the model. Nothing here changes what is shown or saved:
//   1. Each of your messages gets its send time in front of it, e.g. "[2026-10-13 09:44 PDT, Tue] hello".
//      The time is the message's own, so old stamps never change and the prompt cache stays valid.
//   2. Time macros in the system prompt / character card are filled in when a message is sent:
//      {{isodate}} 2026-10-13   {{date}} Oct 13, 2026   {{time}} 09:44   {{weekday}} Tuesday
//      {{datetime}} 2026-10-13 09:44 PDT   {{timezone}} PDT
// Everything uses the clock and time zone of the device you are typing on.

/** Turn off to stop stamping your messages (the macros still work). */
export const STAMP_USER_MESSAGES = true;
/** Rough size of one stamp, so the context gauge counts it. */
export const STAMP_TOKENS = 14;

const pad = (n: number) => String(n).padStart(2, '0');

function zoneName(d: Date): string {
  try {
    const part = new Intl.DateTimeFormat('en-US', { timeZoneName: 'short' }).formatToParts(d).find((p) => p.type === 'timeZoneName');
    return part?.value ?? '';
  } catch {
    return '';
  }
}

export function isoDate(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function clockTime(d: Date): string {
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const weekday = (d: Date, style: 'long' | 'short') => d.toLocaleDateString('en-US', { weekday: style });

/** "2026-10-13 09:44 PDT, Tue" */
export function stampText(d: Date): string {
  const zone = zoneName(d);
  return `${isoDate(d)} ${clockTime(d)}${zone ? ' ' + zone : ''}, ${weekday(d, 'short')}`;
}

/** Puts the stamp in front of a message's text. Works for plain text and for text+image content. */
export function stampContent<T extends string | Array<Record<string, unknown>>>(content: T, created: Date): T {
  const d = created instanceof Date && !isNaN(created.getTime()) ? created : null;
  if (!d) return content;
  const prefix = `[${stampText(d)}] `;
  if (typeof content === 'string') return (prefix + content) as T;
  const parts = content.slice();
  const i = parts.findIndex((p) => p.type === 'text');
  if (i >= 0) parts[i] = { ...parts[i], text: prefix + String(parts[i].text ?? '') };
  else parts.unshift({ type: 'text', text: prefix.trim() });
  return parts as T;
}

/** Fills in {{isodate}}, {{date}}, {{time}}, {{weekday}}, {{datetime}} and {{timezone}}. */
export function resolveTimeMacros(text: string, now: Date = new Date()): string {
  if (!text.includes('{{')) return text;
  const zone = zoneName(now);
  return text
    .replace(/\{\{isodate\}\}/gi, isoDate(now))
    .replace(/\{\{date\}\}/gi, now.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }))
    .replace(/\{\{time\}\}/gi, clockTime(now))
    .replace(/\{\{weekday\}\}/gi, weekday(now, 'long'))
    .replace(/\{\{datetime\}\}/gi, `${isoDate(now)} ${clockTime(now)}${zone ? ' ' + zone : ''}`)
    .replace(/\{\{timezone\}\}/gi, zone);
}
