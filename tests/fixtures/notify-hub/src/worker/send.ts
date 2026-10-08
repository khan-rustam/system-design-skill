import { pool } from "../db";
import { sendEmail } from "../providers/email";
import { sendSms } from "../providers/textblast";

export async function processSend(messageId: string): Promise<void> {
  const { rows } = await pool.query(
    "SELECT id, channel, recipient, subject, body FROM messages WHERE id = $1",
    [messageId],
  );
  if (rows.length === 0) throw new Error(`message ${messageId} not found`);
  const msg = rows[0];

  await pool.query("UPDATE messages SET status = 'sending', updated_at = now() WHERE id = $1", [msg.id]);

  const result =
    msg.channel === "sms"
      ? await sendSms(msg.recipient, msg.body)
      : await sendEmail(msg.recipient, msg.subject, msg.body);

  await pool.query(
    "UPDATE messages SET status = 'sent', provider_ref = $1, sent_at = now(), updated_at = now() WHERE id = $2",
    [result.ref, msg.id],
  );
}
