import { z } from "zod";
import { resolveLockScopes } from "@/planner/locks";
import { getHub } from "@/realtime/hub";
import { isLockKey, type ClaimResponse } from "@/realtime/protocol";
import { routeViewer, sameOrigin } from "@/realtime/route-auth";

export const dynamic = "force-dynamic";

const claimSchema = z.object({
  clientId: z.uuid(),
  keys: z.array(z.string().refine(isLockKey, "Not a lock key.")).max(500),
});

/** Set (and renew) the exact set of locks this client holds. */
export async function POST(request: Request): Promise<Response> {
  if (!sameOrigin(request)) return new Response("Cross-site request refused.", { status: 403 });
  const viewer = await routeViewer(request);
  if (viewer instanceof Response) return viewer;

  const parsed = claimSchema.safeParse(await request.json());
  if (!parsed.success) return new Response(parsed.error.message, { status: 400 });
  const { clientId, keys } = parsed.data;

  const hub = getHub();
  // 409: the event stream dropped (e.g. a server restart); the client reconnects.
  if (hub.viewerOf(clientId)?.kerberos !== viewer.kerberos) {
    return new Response("Not connected.", { status: 409 });
  }
  const { resolved, missing } = await resolveLockScopes(viewer, keys.filter(isLockKey));
  const result = hub.setClaims(clientId, resolved);
  const body: ClaimResponse = { ...result, missing };
  return Response.json(body);
}
