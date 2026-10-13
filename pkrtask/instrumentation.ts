export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const { migrateDatabase } = await import("./db/migrate");
  await migrateDatabase();
}
