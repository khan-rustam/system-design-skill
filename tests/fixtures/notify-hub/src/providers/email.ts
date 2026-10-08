import nodemailer from "nodemailer";
import { config } from "../config";

const transport = nodemailer.createTransport({
  host: config.smtpHost,
  port: 587,
  connectionTimeout: 10_000,
  socketTimeout: 30_000,
});

export async function sendEmail(to: string, subject: string, body: string): Promise<{ ref: string }> {
  const info = await transport.sendMail({ from: "no-reply@notify-hub.example", to, subject, text: body });
  return { ref: info.messageId };
}
