import { config } from "../config";

const BASE = "https://api.textblast.example/v3";

export interface SendResult {
  ref: string;
}

async function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function sendSms(to: string, body: string): Promise<SendResult> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const resp = await fetch(`${BASE}/messages`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${config.textblastApiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ to, body }),
        signal: AbortSignal.timeout(10_000),
      });
      if (!resp.ok) throw new Error(`textblast ${resp.status}`);
      const data = (await resp.json()) as { id: string };
      return { ref: data.id };
    } catch (err) {
      lastError = err;
      await sleep(1000);
    }
  }
  throw lastError;
}
