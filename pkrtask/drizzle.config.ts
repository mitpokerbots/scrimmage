import { defineConfig } from "drizzle-kit";

// Only `drizzle-kit generate` is used; the app applies migrations itself at startup.
export default defineConfig({
  schema: "./db/schema.ts",
  out: "./drizzle",
  dialect: "sqlite",
});
