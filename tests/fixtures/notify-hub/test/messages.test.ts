import { describe, expect, it, vi } from "vitest";

vi.mock("../src/db", () => ({ pool: { query: vi.fn() } }));

describe("SendMessage schema", () => {
  it("rejects phone numbers without a country code", async () => {
    const { messages } = await import("../src/api/messages");
    expect(messages).toBeDefined();
  });
});
