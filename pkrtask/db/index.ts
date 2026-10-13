import { AsyncLocalStorage } from "node:async_hooks";
import { existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import process from "node:process";
import { createClient, type Client } from "@libsql/client";
import { drizzle } from "drizzle-orm/libsql";
import * as schema from "./schema";

function databaseUrl(): string {
  // Next.js loads .env itself; other entrypoints do not.
  if (!process.env.DATABASE_PATH && existsSync(".env")) {
    process.loadEnvFile(".env");
  }
  const path = process.env.DATABASE_PATH;
  if (!path) {
    throw new Error("DATABASE_PATH is not set. Copy .env.example to .env and adjust it.");
  }
  const databasePath = resolve(path);
  mkdirSync(dirname(databasePath), { recursive: true });
  return `file:${databasePath}`;
}

/*
 * libsql runs SQLite synchronously on Node's one thread, from a pool of
 * connections, and a transaction keeps its connection across awaits. Any other
 * write while it is open would block the thread in SQLite's busy handler, so
 * the transaction could never commit. Every database call therefore takes its
 * turn in one queue, and a transaction holds the queue until it settles. Code
 * inside a transaction must use its `tx`; touching `db` there throws instead of
 * deadlocking.
 */
let queueTail: Promise<void> = Promise.resolve();
const insideTransaction = new AsyncLocalStorage<true>();

async function inTurn<T>(work: () => Promise<T>): Promise<T> {
  if (insideTransaction.getStore()) {
    throw new Error("Database used outside its open transaction. Use the transaction's `tx` handle.");
  }
  let release!: () => void;
  const turn = new Promise<void>((resolveTurn) => {
    release = resolveTurn;
  });
  const previous = queueTail;
  queueTail = previous.then(() => turn);
  await previous;
  try {
    return await work();
  } finally {
    release();
  }
}

const QUEUED_METHODS = new Set<PropertyKey>(["execute", "batch", "executeMultiple", "migrate"]);

function createQueuedClient(url: string): Client {
  const raw = createClient({ url, timeout: 5000 });
  return new Proxy(raw, {
    get(target, property, receiver) {
      const value: unknown = Reflect.get(target, property, receiver);
      if (typeof value !== "function") return value;
      const method = value as (...args: unknown[]) => unknown;
      if (!QUEUED_METHODS.has(property)) return method.bind(target);
      return (...args: unknown[]) => inTurn(async () => method.apply(target, args));
    },
  });
}

function createDb() {
  const client = createQueuedClient(databaseUrl());
  const database = drizzle(client, { schema });
  const unqueuedTransaction = database.transaction.bind(database);
  database.transaction = ((run, config) =>
    inTurn(() => insideTransaction.run(true, () => unqueuedTransaction(run, config)))) as typeof database.transaction;
  return { client, database };
}

// Reuse the client across dev hot-reloads so the queue stays single.
const globalForDb = globalThis as unknown as { pkrtaskDb?: ReturnType<typeof createDb> };

function opened(): ReturnType<typeof createDb> {
  globalForDb.pkrtaskDb ??= createDb();
  return globalForDb.pkrtaskDb;
}

/** Opens the database on first use: `next build` imports this module but must not touch it. */
function onFirstUse<T extends object>(get: () => T): T {
  return new Proxy({} as T, {
    get(_target, property) {
      const target = get();
      const value: unknown = Reflect.get(target, property, target);
      return typeof value === "function" ? (value as (...args: unknown[]) => unknown).bind(target) : value;
    },
  });
}

export const client = onFirstUse(() => opened().client);
export const db = onFirstUse(() => opened().database);
