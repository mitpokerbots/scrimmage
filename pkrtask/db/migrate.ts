import { resolve } from "node:path";
import { migrate } from "drizzle-orm/libsql/migrator";
import { client, db } from "./index";

export async function migrateDatabase(): Promise<void> {
  // Persistent: readers never wait on the writer.
  await client.execute("PRAGMA journal_mode = WAL");
  await migrate(db, { migrationsFolder: resolve("drizzle") });
}
