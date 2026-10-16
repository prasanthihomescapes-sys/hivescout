import { describe, expect, it } from "vitest";
import { db, AS_ANON, AS_SERVICE, asUser } from "../harness";

describe("decision_log RLS", () => {
  it("anon cannot call log_decision", () => {
    expect(db.fails(`${AS_ANON} select public.log_decision('{}'::jsonb);`)).toMatch(/permission denied/);
  });

  it("is append-only, even for service_role", async () => {
    await expect(db.exec(`${AS_SERVICE} update public.decision_log set rationale = 'x';`)).rejects.toThrow(/append-only/);
  });

  it("another tenant sees no rows", () => {
    expect(db.rows(`select * from public.decision_log`, asUser("B"))).toHaveLength(0);
  });

  it("rejects an unauthenticated API call", async () => {
    const res = await fetch("/api/decisions", { method: "POST" });
    expect(res.status).toBe(401);
  });
});
