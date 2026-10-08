import express from "express";
import pinoHttp from "pino-http";
import { randomUUID } from "crypto";
import { config } from "../config";
import { logger } from "../logger";
import { messages } from "./messages";
import { providerWebhooks } from "./providerWebhooks";

const app = express();
app.use(express.json({ limit: "256kb" }));
app.use(
  pinoHttp({
    logger,
    genReqId: (req) => (req.headers["x-request-id"] as string) ?? randomUUID(),
  }),
);

app.get("/healthz", (_req, res) => res.json({ ok: true }));
app.use(providerWebhooks);
app.use(messages);

app.listen(config.port, () => logger.info({ port: config.port }, "notify-hub api listening"));
