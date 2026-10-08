import { createHash } from "crypto";
import type { NextFunction, Request, Response } from "express";
import { pool } from "../db";

export interface AuthedRequest extends Request {
  tenantId?: string;
}

export async function requireApiKey(req: AuthedRequest, res: Response, next: NextFunction) {
  const key = req.header("x-api-key");
  if (!key) return res.status(401).json({ error: "missing api key" });

  const keyHash = createHash("sha256").update(key).digest("hex");
  const { rows } = await pool.query(
    "SELECT tenant_id FROM api_keys WHERE key_hash = $1 AND revoked_at IS NULL",
    [keyHash],
  );
  if (rows.length === 0) return res.status(401).json({ error: "invalid api key" });

  req.tenantId = rows[0].tenant_id;
  next();
}
