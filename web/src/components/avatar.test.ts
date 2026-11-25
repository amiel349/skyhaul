import { describe, expect, it } from "vitest";
import { avatarColor, initialsOf } from "./avatar";

describe("avatar helpers", () => {
  it("takes initials from the email's local part", () => {
    expect(initialsOf("amiel.lejzor@x.dev")).toBe("AL");
    expect(initialsOf("dispatcher@x.dev")).toBe("D");
  });

  it("gives the same email the same colour", () => {
    expect(avatarColor("a@skyhaul.dev")).toBe(avatarColor("a@skyhaul.dev"));
  });
});
