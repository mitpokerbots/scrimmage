"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BASE_PATH } from "@/lib/base-path";
import {
  CLAIM_RENEW_MS,
  type ChangedEvent,
  type ClaimResponse,
  type HelloEvent,
  type LockKey,
  type LockView,
  type PresenceEvent,
} from "./protocol";

export type Realtime = {
  /** Everyone else with this plan open. */
  others: { kerberos: string; name: string }[];
  /** Locks held by other people, by key. */
  lockedByOthers: ReadonlyMap<LockKey, LockView>;
  /** Make `keys` exactly what this tab holds locked (renewed automatically). */
  claim: (keys: LockKey[]) => void;
};

/**
 * Live collaboration for one open plan page. `onChanged` is called with the
 * ids of plans someone edited (including this viewer, so state converges after
 * every save); the caller decides whether it is showing any of them.
 */
export function useRealtime(
  planId: string,
  viewerKerberos: string,
  onChanged: (planIds: string[]) => void,
): Realtime {
  const [presence, setPresence] = useState<PresenceEvent>({ viewers: [], locks: [] });
  const clientIdRef = useRef<string | null>(null);
  const wantedRef = useRef<LockKey[]>([]);
  const onChangedRef = useRef(onChanged);
  useEffect(() => {
    onChangedRef.current = onChanged;
  });

  const sendClaims = useCallback(async () => {
    const clientId = clientIdRef.current;
    if (!clientId) return;
    const response = await fetch(`${BASE_PATH}/api/realtime/claims`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clientId, keys: wantedRef.current }),
    });
    // 409: the stream dropped; EventSource reconnects and `hello` re-sends claims.
    if (response.status === 409) return;
    if (!response.ok) {
      throw new Error(`Claiming locks failed: HTTP ${response.status} ${await response.text()}`);
    }
    const result = (await response.json()) as ClaimResponse;
    if (result.missing.length > 0) {
      const missing = new Set(result.missing);
      wantedRef.current = wantedRef.current.filter((key) => !missing.has(key));
    }
  }, []);

  const claim = useCallback(
    (keys: LockKey[]) => {
      const next = [...new Set(keys)].sort();
      const current = wantedRef.current;
      if (next.length === current.length && next.every((key, i) => key === current[i])) return;
      wantedRef.current = next;
      void sendClaims();
    },
    [sendClaims],
  );

  useEffect(() => {
    const source = new EventSource(`${BASE_PATH}/api/realtime?planId=${encodeURIComponent(planId)}`);
    source.addEventListener("hello", (event) => {
      clientIdRef.current = (JSON.parse((event as MessageEvent<string>).data) as HelloEvent).clientId;
      if (wantedRef.current.length > 0) void sendClaims();
    });
    source.addEventListener("presence", (event) => {
      setPresence(JSON.parse((event as MessageEvent<string>).data) as PresenceEvent);
    });
    source.addEventListener("changed", (event) => {
      onChangedRef.current((JSON.parse((event as MessageEvent<string>).data) as ChangedEvent).planIds);
    });
    const renew = window.setInterval(() => {
      if (wantedRef.current.length > 0) void sendClaims();
    }, CLAIM_RENEW_MS);
    return () => {
      window.clearInterval(renew);
      source.close();
      clientIdRef.current = null;
    };
  }, [planId, sendClaims]);

  const others = useMemo(
    () => presence.viewers.filter((v) => v.kerberos !== viewerKerberos),
    [presence, viewerKerberos],
  );
  const lockedByOthers = useMemo(
    () => new Map(presence.locks.filter((l) => l.kerberos !== viewerKerberos).map((l) => [l.key, l])),
    [presence, viewerKerberos],
  );
  return { others, lockedByOthers, claim };
}
