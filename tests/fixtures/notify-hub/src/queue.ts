import { Queue } from "bullmq";
import IORedis from "ioredis";
import { config } from "./config";

export const connection = new IORedis(config.redisUrl, { maxRetriesPerRequest: null });

export const sendQueue = new Queue("send", {
  connection,
  defaultJobOptions: {
    attempts: 5,
    removeOnComplete: true,
    removeOnFail: true,
  },
});
