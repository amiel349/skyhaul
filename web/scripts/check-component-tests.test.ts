// @vitest-environment node
import { describe, expect, it } from "vitest";
// @ts-expect-error plain Node script without types
import { missingTests, testFileFor } from "./check-component-tests.mjs";

describe("check-component-tests", () => {
  it("names every .vue file that has no sibling test file", () => {
    const tests = new Set(["src/components/A.test.ts"]);
    const missing = missingTests(["src/components/A.vue", "src/views/B.vue"], (file: string) => tests.has(file));

    expect(missing).toEqual(["src/views/B.vue"]);
    expect(testFileFor("src/views/B.vue")).toBe("src/views/B.test.ts");
  });
});
