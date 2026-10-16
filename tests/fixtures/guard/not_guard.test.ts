import { describe, expect, it } from "vitest";
import { parseCaption } from "./caption";

describe("parseCaption", () => {
  it("throws on empty input", () => {
    expect(() => parseCaption("")).toThrow(/empty/);
  });

  it("rejects a caption over the limit", async () => {
    await expect(parseCaption("x".repeat(3000))).rejects.toThrow();
  });
});
