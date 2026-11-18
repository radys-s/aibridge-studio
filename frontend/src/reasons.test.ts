import { describe, expect, it } from "vitest";
import { explainReason } from "./reasons";

// Real decision reasons written by backend/src/studio/runner.py.
describe("explainReason", () => {
  it("tells what to start when the gateway is down", () => {
    const r = explainReason(
      "drafting by autopilot failed: OmniRoute is not reachable: OmniRoute not reachable at http://127.0.0.1:20128",
    );
    expect(r).toMatch(/OmniRoute nebo Refrakt/);
    expect(r).toMatch(/Zkusit fázi znovu/);
  });

  it("says to wait when every model is cooling down", () => {
    const r = explainReason(
      "review by autopilot failed: all lead models failed, are excluded or are cooling down: refrakt/groq/openai/gpt-oss-120b (cooldown)",
    );
    expect(r).toMatch(/pauzu/);
    expect(r).toMatch(/za pár minut/);
  });

  it("explains an incomplete review", () => {
    expect(
      explainReason("Review by autopilot: Review must assess every task criterion exactly once; missing or duplicate coverage."),
    ).toMatch(/každé kritérium/);
  });

  it("counts the rounds when verification keeps failing", () => {
    const r = explainReason("independent verification failed; iteration limit 3 reached");
    expect(r).toMatch(/ani po 3 pokusech/i);
    expect(r).toMatch(/Testy/);
  });

  it("keeps the reviewer's words when the approach is rejected", () => {
    expect(explainReason("reviewer rejected the approach: uses a global variable")).toMatch(
      /uses a global variable/,
    );
  });

  it("names the phase of an unknown model failure", () => {
    expect(explainReason("planning by autopilot failed: something odd")).toMatch(/^Plán se nepovedl/);
  });

  it("returns null for text it does not know, so the original is shown", () => {
    expect(explainReason("Nějaký český důvod.")).toBeNull();
    expect(explainReason("")).toBeNull();
  });
});
