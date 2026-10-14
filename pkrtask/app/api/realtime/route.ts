import { eq } from "drizzle-orm";
import { z } from "zod";
import { db } from "@/db";
import { plans } from "@/db/schema";
import { canSee } from "@/planner/access";
import { getHub } from "@/realtime/hub";
import { routeViewer } from "@/realtime/route-auth";

export const dynamic = "force-dynamic";

// Keeps Apache's proxy timeout and any idle-connection reaping at bay.
const HEARTBEAT_MS = 15_000;

/** Server-sent events for one open plan page: hello, presence, changed. */
export async function GET(request: Request): Promise<Response> {
  const viewer = await routeViewer(request);
  if (viewer instanceof Response) return viewer;

  const planId = z.uuid().safeParse(new URL(request.url).searchParams.get("planId"));
  if (!planId.success) return new Response("Bad planId.", { status: 400 });
  const plan = await db.query.plans.findFirst({ where: eq(plans.id, planId.data) });
  if (!plan || !canSee(plan, viewer)) return new Response("Not found.", { status: 404 });

  const hub = getHub();
  const encoder = new TextEncoder();
  let closed = false;
  let clientId: string | null = null;
  let heartbeat: ReturnType<typeof setInterval> | undefined;

  const close = () => {
    if (closed) return;
    closed = true;
    clearInterval(heartbeat);
    if (clientId) hub.disconnect(clientId);
  };

  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const write = (text: string) => {
        if (!closed) controller.enqueue(encoder.encode(text));
      };
      clientId = hub.connect(viewer, plan.id, (event, data) => {
        write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
      });
      heartbeat = setInterval(() => write(": ping\n\n"), HEARTBEAT_MS);
      request.signal.addEventListener("abort", close);
    },
    cancel: close,
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      "X-Accel-Buffering": "no",
    },
  });
}
