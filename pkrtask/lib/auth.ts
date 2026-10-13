import { headers } from "next/headers";
import { notFound, redirect } from "next/navigation";
import { cache } from "react";
import { z } from "zod";
import { BASE_PATH } from "./base-path";

/*
 * Pkrtask has no login of its own. Scrimmage's Flask app owns the session
 * (Touchstone, or dev login) and admin status; we forward the browser's cookie
 * to its internal /api/pkrtask/session endpoint, which Apache only serves on
 * the loopback-only internal port.
 */

export type Person = { kerberos: string; name: string | null };
export type Viewer = Person;

const personSchema = z
  .object({ kerberos: z.string().min(1), display_name: z.string().nullable() })
  .transform((p): Person => ({ kerberos: p.kerberos, name: p.display_name }));

const sessionSchema = z.object({ viewer: personSchema, admins: z.array(personSchema) });

type Session =
  | { status: "admin"; viewer: Viewer; admins: Person[] }
  | { status: "logged-out" }
  | { status: "not-admin" };

function requiredEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is not set. Copy .env.example to .env and adjust it.`);
  }
  return value.replace(/\/+$/, "");
}

/** One lookup per request, shared by the page and everything it renders. */
const loadSession = cache(async (): Promise<Session> => {
  const url = `${requiredEnv("SCRIMMAGE_INTERNAL_URL")}/api/pkrtask/session`;
  const cookie = (await headers()).get("cookie") ?? "";
  const response = await fetch(url, { headers: { cookie }, cache: "no-store", redirect: "manual" });
  if (response.status === 401) return { status: "logged-out" };
  if (response.status === 403) return { status: "not-admin" };
  if (response.status !== 200) {
    throw new Error(`Scrimmage session check at ${url} returned HTTP ${response.status}.`);
  }
  const session = sessionSchema.parse(await response.json());
  return { status: "admin", ...session };
});

/** For pages: send logged-out visitors to scrimmage's login; hide Pkrtask from non-admins. */
export async function getPageViewer(currentPath: string): Promise<Viewer> {
  const session = await loadSession();
  if (session.status === "admin") return session.viewer;
  if (session.status === "not-admin") notFound();
  // Absolute, so Next does not prefix it with the /pkrtask base path.
  const next = encodeURIComponent(currentPath === "/" ? BASE_PATH : `${BASE_PATH}${currentPath}`);
  redirect(`${requiredEnv("PUBLIC_URL")}/login?next=${next}`);
}

/** For server actions: errors surface in the UI rather than redirecting mid-edit. */
export async function getViewer(): Promise<Viewer> {
  const session = await loadSession();
  if (session.status === "admin") return session.viewer;
  if (session.status === "not-admin") throw new Error("Pkrtask is only available to scrimmage admins.");
  throw new Error("Your scrimmage login has expired. Reload the page to log in again.");
}

/** Everyone who can be assigned work: scrimmage's admins. */
export async function listPeople(): Promise<Person[]> {
  const session = await loadSession();
  if (session.status !== "admin") {
    throw new Error("Pkrtask is only available to scrimmage admins.");
  }
  return session.admins;
}
