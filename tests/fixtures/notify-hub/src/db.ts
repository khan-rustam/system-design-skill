import { Pool } from "pg";
import { config } from "./config";

export const pool = new Pool({
  connectionString: config.databaseUrl,
  max: 20,
  connectionTimeoutMillis: 5_000,
  statement_timeout: 10_000,
  idleTimeoutMillis: 30_000,
});
