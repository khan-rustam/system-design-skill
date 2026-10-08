export const config = {
  port: Number(process.env.PORT ?? 3000),
  databaseUrl: process.env.DATABASE_URL || "postgres://notify:notify@localhost:5432/notify",
  redisUrl: process.env.REDIS_URL || "redis://localhost:6379",
  textblastApiKey: process.env.TEXTBLAST_API_KEY ?? "",
  textblastWebhookSecret: process.env.TEXTBLAST_WEBHOOK_SECRET ?? "",
  smtpHost: process.env.SMTP_HOST ?? "smtp.internal",
  workerConcurrency: Number(process.env.WORKER_CONCURRENCY ?? 50),
};
