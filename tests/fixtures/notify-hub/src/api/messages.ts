import { Router } from "express";
import { z } from "zod";
import { pool } from "../db";
import { sendQueue } from "../queue";
import { checkAndCountQuota } from "../quota";
import { AuthedRequest, requireApiKey } from "./auth";

export const messages = Router();
messages.use(requireApiKey);

const SendMessage = z.discriminatedUnion("channel", [
  z.object({
    channel: z.literal("sms"),
    to: z.string().regex(/^\+[1-9]\d{6,14}$/),
    body: z.string().min(1).max(1600),
  }),
  z.object({
    channel: z.literal("email"),
    to: z.string().email(),
    subject: z.string().min(1).max(200),
    body: z.string().min(1).max(100_000),
  }),
]);

messages.post("/v1/messages", async (req: AuthedRequest, res) => {
  const parsed = SendMessage.safeParse(req.body);
  if (!parsed.success) return res.status(400).json({ error: parsed.error.flatten() });

  if (!(await checkAndCountQuota(req.tenantId!))) {
    return res.status(429).json({ error: "daily message limit reached" });
  }

  const msg = parsed.data;
  const { rows } = await pool.query(
    `INSERT INTO messages (tenant_id, channel, recipient, subject, body, status)
     VALUES ($1, $2, $3, $4, $5, 'queued') RETURNING id, status, created_at`,
    [req.tenantId, msg.channel, msg.to, msg.channel === "email" ? msg.subject : null, msg.body],
  );
  await sendQueue.add("send", { messageId: rows[0].id });

  res.status(202).json(rows[0]);
});

messages.get("/v1/messages/:id", async (req: AuthedRequest, res) => {
  const { rows } = await pool.query(
    "SELECT id, channel, recipient, status, provider_ref, error, created_at, sent_at FROM messages WHERE id = $1",
    [req.params.id],
  );
  if (rows.length === 0) return res.status(404).json({ error: "not found" });
  res.json(rows[0]);
});

messages.get("/v1/messages", async (req: AuthedRequest, res) => {
  const { rows } = await pool.query(
    `SELECT id, channel, recipient, status, created_at
     FROM messages WHERE tenant_id = $1 ORDER BY created_at DESC`,
    [req.tenantId],
  );
  res.json({ data: rows });
});
