// vite-plugin-data.ts
//
// File-backed JSON API for the frontend, served from the same Vite server
// (dev and preview) so it shares the frontend's origin and port. No extra container.
//
//   GET    /api/data/settings                 -> <dataDir>/settings.json
//   PUT    /api/data/settings
//   GET    /api/data/state                    -> <dataDir>/state.json  (small shared UI state)
//   PUT    /api/data/state
//   GET    /api/data/characters               -> list of cards
//   GET    /api/data/characters/:id           -> <dataDir>/characters/:id.json
//   PUT    /api/data/characters/:id
//   DELETE /api/data/characters/:id
//   (same for /lorebooks)
//
// Every doc comes back as { id?, version, data }. `version` is a hash of the file
// contents. PUT must send it back as `If-Match`; if the file changed in between
// (e.g. you saved from your other device), the server answers 409 with the
// current copy instead of overwriting it.
//
// Data dir: options.dataDir, else $DATA_DIR (or the older $LYRA_DATA_DIR), else ./data (relative to the
// directory Vite is started from).

import crypto from 'node:crypto';
import path from 'node:path';
import { promises as fs } from 'node:fs';
import type { IncomingMessage, ServerResponse } from 'node:http';
import type { Plugin } from 'vite';

export interface DataPluginOptions {
  dataDir?: string;
}

type Next = (err?: unknown) => void;

const MAX_BODY_BYTES = 5 * 1024 * 1024;
const COLLECTIONS = new Set(['characters', 'lorebooks']);
// Single documents stored as <dataDir>/<name>.json (no ids, no listing).
const SINGLETONS = new Set(['settings', 'state']);
const ID_RE = /^[A-Za-z0-9_-]{1,80}$/;

const versionOf = (text: string): string => crypto.createHash('sha1').update(text).digest('hex').slice(0, 12);

function send(res: ServerResponse, status: number, body?: unknown, headers: Record<string, string> = {}): void {
  const isObj = body !== undefined && typeof body === 'object';
  res.writeHead(status, {
    ...(isObj ? { 'Content-Type': 'application/json; charset=utf-8' } : {}),
    'Cache-Control': 'no-store',
    ...headers
  });
  res.end(isObj ? JSON.stringify(body) : undefined);
}

class HttpError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function readBody(req: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    const buf = chunk as Buffer;
    size += buf.length;
    if (size > MAX_BODY_BYTES) throw new HttpError(413, 'Body too large');
    chunks.push(buf);
  }
  return Buffer.concat(chunks).toString('utf8');
}

// One write at a time per file, so the version check and the write can't interleave.
const locks = new Map<string, Promise<unknown>>();
function withLock<R>(key: string, fn: () => Promise<R>): Promise<R> {
  const prev = locks.get(key) ?? Promise.resolve();
  const run = prev.then(fn);
  const tail = run.catch(() => {});
  locks.set(key, tail);
  void tail.then(() => {
    if (locks.get(key) === tail) locks.delete(key);
  });
  return run;
}

interface DocOnDisk {
  data: unknown;
  version: string;
}

async function readDoc(file: string): Promise<DocOnDisk | null> {
  try {
    const text = await fs.readFile(file, 'utf8');
    return { data: JSON.parse(text), version: versionOf(text) };
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') return null;
    throw err;
  }
}

// Write to a temp file, then rename. A crash or dropped connection can't leave a half-written card.
async function writeAtomic(file: string, text: string): Promise<void> {
  await fs.mkdir(path.dirname(file), { recursive: true });
  const tmp = `${file}.${process.pid}.${Date.now()}.tmp`;
  await fs.writeFile(tmp, text, 'utf8');
  await fs.rename(tmp, file);
}

function createHandler(dataDir: string) {
  const docPath = (collection: string, id?: string): string =>
    SINGLETONS.has(collection) ? path.join(dataDir, `${collection}.json`) : path.join(dataDir, collection, `${id}.json`);

  async function handle(req: IncomingMessage, res: ServerResponse, url: URL): Promise<void> {
    const m = url.pathname.match(/^\/api\/data\/([a-z]+)(?:\/([^/]+))?\/?$/);
    if (!m) return send(res, 404, { error: 'not_found' });

    const [, collection, rawId] = m;
    const isSettings = SINGLETONS.has(collection);
    if (!isSettings && !COLLECTIONS.has(collection)) return send(res, 404, { error: 'unknown_collection' });

    let id: string | undefined;
    try {
      id = rawId ? decodeURIComponent(rawId) : undefined;
    } catch {
      return send(res, 400, { error: 'invalid_id' });
    }
    if (isSettings && id) return send(res, 404, { error: 'not_found' });
    if (id && !ID_RE.test(id)) return send(res, 400, { error: 'invalid_id' });

    // List a collection
    if (!isSettings && !id) {
      if (req.method !== 'GET') return send(res, 405, { error: 'method_not_allowed' });
      const dir = path.join(dataDir, collection);
      let names: string[] = [];
      try {
        names = await fs.readdir(dir);
      } catch (err) {
        if ((err as NodeJS.ErrnoException).code !== 'ENOENT') throw err;
      }
      const items: unknown[] = [];
      for (const name of names.sort()) {
        if (!name.endsWith('.json')) continue;
        const itemId = name.slice(0, -5);
        if (!ID_RE.test(itemId)) continue;
        try {
          const doc = await readDoc(path.join(dir, name));
          if (doc) items.push({ id: itemId, ...doc });
        } catch (err) {
          console.warn(`[data-api] skipping unreadable ${collection}/${name}: ${(err as Error).message}`);
        }
      }
      return send(res, 200, items);
    }

    const file = docPath(collection, id);
    const withId = (doc: DocOnDisk) => (id ? { id, ...doc } : doc);

    if (req.method === 'GET') {
      const doc = await readDoc(file);
      return doc ? send(res, 200, withId(doc)) : send(res, 404, { error: 'not_found' });
    }

    if (req.method === 'PUT') {
      let parsed: unknown;
      try {
        parsed = JSON.parse(await readBody(req));
      } catch (err) {
        if (err instanceof HttpError) throw err;
        return send(res, 400, { error: 'invalid_json' });
      }
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return send(res, 400, { error: 'body_must_be_object' });
      }
      const ifMatch = req.headers['if-match'];
      const text = JSON.stringify(parsed, null, 2) + '\n';

      return withLock(file, async () => {
        const current = await readDoc(file);
        // Existing file: caller must prove it saw the latest version.
        // No file: caller must not claim a version.
        const stale = current ? ifMatch !== current.version : ifMatch !== undefined;
        if (stale) return send(res, 409, { error: 'conflict', current: current ? withId(current) : null });
        await writeAtomic(file, text);
        return send(res, 200, { version: versionOf(text) });
      });
    }

    if (req.method === 'DELETE') {
      return withLock(file, async () => {
        try {
          await fs.unlink(file);
        } catch (err) {
          if ((err as NodeJS.ErrnoException).code !== 'ENOENT') throw err;
        }
        return send(res, 204);
      });
    }

    return send(res, 405, { error: 'method_not_allowed' });
  }

  // Connect-style middleware: only touches /api/data, passes everything else through.
  return (req: IncomingMessage, res: ServerResponse, next: Next): void => {
    const url = new URL(req.url ?? '/', 'http://localhost');
    if (url.pathname !== '/api/data' && !url.pathname.startsWith('/api/data/')) return next();

    handle(req, res, url).catch((err: unknown) => {
      if (res.headersSent) return void res.end();
      if (err instanceof HttpError) {
        // Expected client error. Close the connection: we stopped reading the request body.
        return send(res, err.status, { error: err.message }, { Connection: 'close' });
      }
      console.error('[data-api]', err);
      send(res, 500, { error: 'internal_error' });
    });
  };
}

export default function dataPlugin(options: DataPluginOptions = {}): Plugin {
  const dataDir = path.resolve(options.dataDir ?? process.env.DATA_DIR ?? process.env.LYRA_DATA_DIR ?? 'data');
  const handler = createHandler(dataDir);

  return {
    name: 'data-api',
    configureServer(server) {
      // Registered directly (not in a returned function) so it runs before Vite's own proxy.
      server.middlewares.use(handler);
      console.log(`[data-api] serving /api/data from ${dataDir}`);
    },
    configurePreviewServer(server) {
      server.middlewares.use(handler);
    }
  };
}

// Exported for tests.
export { createHandler };
