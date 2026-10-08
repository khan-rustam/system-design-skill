import { Router } from "express";
import { pool } from "../db";
import { logger } from "../logger";

export const providerWebhooks = Router();

providerWebhooks.post("/webhooks/textblast/status", async (req, res) => {
  try {
    const { message_ref, status, error_code } = req.body;
    const newStatus = status === "DELIVERED" ? "delivered" : "failed";
    await pool.query(
      "UPDATE messages SET status = $1, error = $2, updated_at = now() WHERE provider_ref = $3",
      [newStatus, error_code ?? null, message_ref],
    );
  } catch (err) {
    logger.warn({ err }, "could not apply delivery receipt");
  }
  res.sendStatus(200);
});
