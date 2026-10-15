// src/lib/api.ts
//
// One place for talking to the backend services. Every call either returns
// the parsed JSON or throws an Error whose message is safe to show on screen,
// so a failed save or delete can never look like "nothing happened".

async function describeFailure(res: Response): Promise<string> {
  let detail = '';
  try {
    const body = await res.json();
    if (typeof body?.detail === 'string') {
      detail = body.detail;
    } else if (Array.isArray(body?.detail)) {
      // FastAPI validation errors: [{ loc: ['body', 'field'], msg: '...' }, ...]
      detail = body.detail
        .map((item: any) => {
          const field = Array.isArray(item?.loc) ? item.loc.slice(1).join('.') : '';
          return field ? `${field}: ${item?.msg}` : String(item?.msg ?? '');
        })
        .join('; ');
    }
  } catch {
    // Response body wasn't JSON; fall through to the generic message.
  }
  return detail ? `${detail} (${res.status})` : `Request failed (${res.status} ${res.statusText})`;
}

export async function api<T = any>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const init: RequestInit = { method };
  if (body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(body);
  }

  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new Error('Could not reach the server.');
  }

  if (!res.ok) {
    throw new Error(await describeFailure(res));
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

/** Short-term memory service (PostgreSQL), reached through the Vite proxy. */
export function stm<T = any>(path: string, method = 'GET', body?: unknown): Promise<T> {
  return api<T>(`/api/stm${path}`, method, body);
}

/** Long-term memory engine (LanceDB). The Vite proxy adds the API key. */
export function memory<T = any>(path: string, method = 'GET', body?: unknown): Promise<T> {
  return api<T>(`/api/memory${path}`, method, body);
}

/** Turn anything thrown into a line of text for an error banner. */
export function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
