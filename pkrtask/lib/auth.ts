import { headers } from "next/headers";
import { notFound, redirect } from "next/navigation";
import { cache } from "react";
import { z } from "zod";
import { ROSTER } from "@/roster";
import { BASE_PATH } from "./base-path";

/*
 * Pkrtask has no login of its own. Scrimmage's Flask app owns the session
 * (Touchstone, or dev login); we forward the browser's cookie to its internal
 * /api/pkrtask/session endpoint, which Apache only serves on the loopback-only
 * internal port. Being on the roster (roster.ts) is what grants access.
 */

export type Person = { kerberos: string; name: string | null };
export type Viewer = { kerberos: string; name: string };

const sessionSchema = z.object({
  viewer: z.object({ kerberos: z.string().min(1), display_name: z.string().nullable() }),
});

type Session =
  | { status: "member"; viewer: Viewer }
  | { status: "logged-out" }
  | { status: "not-member" };

function requiredEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is not set. Copy .env.example to .env and adjust it.`);
  }
  return value.replace(/\/+$/, "");
}

/** Who is logged into scrimmage, given the browser's Cookie header. */
export async function sessionForCookie(cookie: string): Promise<Session> {
  const url = `${requiredEnv("SCRIMMAGE_INTERNAL_URL")}/api/pkrtask/session`;
  const response = await fetch(url, { headers: { cookie }, cache: "no-store", redirect: "manual" });
  if (response.status === 401) return { status: "logged-out" };
  if (response.status !== 200) {
    throw new Error(`Scrimmage session check at ${url} returned HTTP ${response.status}.`);
  }
  const { viewer } = sessionSchema.parse(await response.json());
  const member = ROSTER.find((p) => p.kerberos === viewer.kerberos);
  if (!member) return { status: "not-member" };
  return { status: "member", viewer: { kerberos: member.kerberos, name: member.name } };
}

/** One lookup per request, shared by the page and everything it renders. */
const loadSession = cache(async (): Promise<Session> => {
  return sessionForCookie((await headers()).get("cookie") ?? "");
});

/** For pages: send logged-out visitors to scrimmage's login; hide Pkrtask from everyone else. */
export async function getPageViewer(currentPath: string): Promise<Viewer> {
  const session = await loadSession();
  if (session.status === "member") return session.viewer;
  if (session.status === "not-member") notFound();
  const next = encodeURIComponent(currentPath === "/" ? BASE_PATH : `${BASE_PATH}${currentPath}`);
  // Absolute, so Next does not prefix it with the /pkrtask base path.
  redirect(`${requiredEnv("PUBLIC_URL")}/login?next=${next}`);
}

/** For server actions: errors surface in the UI rather than redirecting mid-edit. */
export async function getViewer(): Promise<Viewer> {
  const session = await loadSession();
  if (session.status === "member") return session.viewer;
  if (session.status === "not-member") throw new Error("You are not on the Pkrtask roster.");
  throw new Error("Your scrimmage login has expired. Reload the page to log in again.");
}

/** Everyone who can be assigned work. */
export function listPeople(): Person[] {
  return ROSTER.map((p) => ({ kerberos: p.kerberos, name: p.name }));
}
