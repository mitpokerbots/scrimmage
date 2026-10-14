import { sessionForCookie, type Viewer } from "@/lib/auth";

/** The roster member making this API request, or the response to send instead. */
export async function routeViewer(request: Request): Promise<Viewer | Response> {
  const session = await sessionForCookie(request.headers.get("cookie") ?? "");
  if (session.status === "logged-out") return new Response("Log in again.", { status: 401 });
  if (session.status === "not-member") return new Response("Not found.", { status: 404 });
  return session.viewer;
}

/**
 * State-changing requests must come from this site. (Server actions get this
 * check from Next; plain route handlers do not.)
 */
export function sameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  if (!origin || !host) return false;
  return URL.canParse(origin) && new URL(origin).host === host;
}
