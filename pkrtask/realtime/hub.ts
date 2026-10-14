import type { Viewer } from "@/lib/auth";
import { canSee, type PlanScope } from "@/planner/access";
import type { ChangedEvent, HelloEvent, LockKey, LockView, PresenceEvent } from "./protocol";

/*
 * Live collaboration state for the one Next.js process: who has which plan
 * open, and editing locks. Locks are leases: a client renews its claims every
 * few seconds; a lock lapses LOCK_TTL_MS after its last renewal, or at once
 * when its client disconnects. Server actions refuse to change anything locked
 * by someone else (planner/locks.ts), so two people never edit the same task.
 */

export const LOCK_TTL_MS = 30_000;
const SWEEP_MS = 5_000;

type Connection = {
  clientId: string;
  viewer: Viewer;
  /** The plan page this client has open. */
  planId: string;
  send: (event: string, data: unknown) => void;
};

type Lock = LockView & { clientId: string; scope: PlanScope; expiresAt: number };

export type ClaimResult = { granted: LockKey[]; denied: LockView[] };

class Hub {
  private readonly connections = new Map<string, Connection>();
  private readonly locks = new Map<LockKey, Lock>();

  constructor() {
    setInterval(() => this.sweep(), SWEEP_MS).unref();
  }

  connect(viewer: Viewer, planId: string, send: Connection["send"]): string {
    const clientId = crypto.randomUUID();
    this.connections.set(clientId, { clientId, viewer, planId, send });
    const hello: HelloEvent = { clientId };
    send("hello", hello);
    this.broadcastPresence();
    return clientId;
  }

  disconnect(clientId: string): void {
    this.connections.delete(clientId);
    for (const [key, lock] of this.locks) {
      if (lock.clientId === clientId) this.locks.delete(key);
    }
    this.broadcastPresence();
  }

  /** The viewer behind a client id, or null if it is not connected. */
  viewerOf(clientId: string): Viewer | null {
    return this.connections.get(clientId)?.viewer ?? null;
  }

  /**
   * Make `wanted` exactly the set of locks this client holds: renew or take
   * each one that is free (or already this person's), release the rest.
   */
  setClaims(clientId: string, wanted: { key: LockKey; scope: PlanScope }[]): ClaimResult {
    const connection = this.connections.get(clientId);
    if (!connection) throw new Error("Not connected.");
    const { viewer } = connection;
    const now = Date.now();
    const wantedKeys = new Set(wanted.map((w) => w.key));
    let changed = false;

    for (const [key, lock] of this.locks) {
      if (lock.clientId === clientId && !wantedKeys.has(key)) {
        this.locks.delete(key);
        changed = true;
      }
    }

    const result: ClaimResult = { granted: [], denied: [] };
    for (const { key, scope } of wanted) {
      const current = this.locks.get(key);
      if (current && current.expiresAt > now && current.kerberos !== viewer.kerberos) {
        result.denied.push({ key, kerberos: current.kerberos, name: current.name });
        continue;
      }
      if (!current || current.clientId !== clientId || current.expiresAt <= now) changed = true;
      this.locks.set(key, {
        key,
        kerberos: viewer.kerberos,
        name: viewer.name,
        clientId,
        scope,
        expiresAt: now + LOCK_TTL_MS,
      });
      result.granted.push(key);
    }

    if (changed) this.broadcastPresence();
    return result;
  }

  /** The first live lock among `keys` held by someone other than `kerberos`. */
  heldByOther(keys: Iterable<LockKey>, kerberos: string): LockView | null {
    const now = Date.now();
    for (const key of keys) {
      const lock = this.locks.get(key);
      if (lock && lock.expiresAt > now && lock.kerberos !== kerberos) {
        return { key, kerberos: lock.kerberos, name: lock.name };
      }
    }
    return null;
  }

  /** Tell every client that can see them that these plans changed. */
  publishChanged(changedPlans: { id: string; scope: PlanScope }[], by: string): void {
    for (const connection of this.connections.values()) {
      const planIds = changedPlans.filter((p) => canSee(p.scope, connection.viewer)).map((p) => p.id);
      if (planIds.length === 0) continue;
      const event: ChangedEvent = { planIds, by };
      connection.send("changed", event);
    }
  }

  private broadcastPresence(): void {
    for (const connection of this.connections.values()) {
      const viewers = new Map<string, string>();
      for (const other of this.connections.values()) {
        if (other.planId === connection.planId) viewers.set(other.viewer.kerberos, other.viewer.name);
      }
      const locks: LockView[] = [];
      for (const lock of this.locks.values()) {
        if (canSee(lock.scope, connection.viewer)) {
          locks.push({ key: lock.key, kerberos: lock.kerberos, name: lock.name });
        }
      }
      const event: PresenceEvent = {
        viewers: [...viewers].map(([kerberos, name]) => ({ kerberos, name })),
        locks,
      };
      connection.send("presence", event);
    }
  }

  private sweep(): void {
    const now = Date.now();
    let changed = false;
    for (const [key, lock] of this.locks) {
      if (lock.expiresAt <= now) {
        this.locks.delete(key);
        changed = true;
      }
    }
    if (changed) this.broadcastPresence();
  }
}

// One hub per process, shared by route handlers and server actions (which Next
// may bundle separately) and kept across dev hot reloads.
const globalForHub = globalThis as unknown as { pkrtaskHub?: Hub };

export function getHub(): Hub {
  globalForHub.pkrtaskHub ??= new Hub();
  return globalForHub.pkrtaskHub;
}
