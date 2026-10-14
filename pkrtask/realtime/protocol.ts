/** Messages between the live-collaboration server and the canvas. */

/** What can be locked for editing: a task, a sub-plan node, or a plan's details. */
export type LockKey = `task:${string}` | `link:${string}` | `plan:${string}`;

export type LockView = { key: LockKey; kerberos: string; name: string };

/** `hello`: sent first on each event stream. */
export type HelloEvent = { clientId: string };

/** `presence`: who has this plan open, and every lock this viewer may know about. */
export type PresenceEvent = {
  viewers: { kerberos: string; name: string }[];
  locks: LockView[];
};

/** `changed`: these plans were edited by `by` (a kerberos). */
export type ChangedEvent = { planIds: string[]; by: string };

export type ClaimRequest = { clientId: string; keys: LockKey[] };
/** `missing`: deleted (or never visible); the client should stop claiming them. */
export type ClaimResponse = { granted: LockKey[]; denied: LockView[]; missing: LockKey[] };

/** Clients renew their claims this often; the server lets them lapse after 30s. */
export const CLAIM_RENEW_MS = 10_000;

export function isLockKey(value: string): value is LockKey {
  return /^(task|link|plan):[0-9a-f-]{36}$/.test(value);
}
