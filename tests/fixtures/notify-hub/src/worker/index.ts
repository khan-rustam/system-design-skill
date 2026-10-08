import { Worker } from "bullmq";
import { config } from "../config";
import { logger } from "../logger";
import { connection } from "../queue";
import { processSend } from "./send";

const worker = new Worker(
  "send",
  async (job) => {
    await processSend(job.data.messageId);
  },
  { connection, concurrency: config.workerConcurrency },
);

worker.on("failed", (job, err) => {
  logger.error({ jobId: job?.id, err }, "send job failed");
});

logger.info({ concurrency: config.workerConcurrency }, "notify-hub worker started");
