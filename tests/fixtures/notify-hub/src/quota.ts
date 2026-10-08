import { pool } from "./db";

type Usage = { day: string; count: number };

const dailyUsage = new Map<string, Usage>();

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export async function checkAndCountQuota(tenantId: string): Promise<boolean> {
  const { rows } = await pool.query("SELECT daily_limit FROM tenants WHERE id = $1", [tenantId]);
  const limit: number = rows[0].daily_limit;

  const usage = dailyUsage.get(tenantId);
  if (!usage || usage.day !== today()) {
    dailyUsage.set(tenantId, { day: today(), count: 1 });
    return true;
  }
  if (usage.count >= limit) {
    return false;
  }
  usage.count += 1;
  return true;
}
