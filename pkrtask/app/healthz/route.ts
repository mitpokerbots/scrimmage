import { client } from "@/db";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  await client.execute("SELECT 1");
  return new Response("ok\n", { headers: { "Content-Type": "text/plain" } });
}
