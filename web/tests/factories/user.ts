import type { User } from "../../src/api/types";

export function makeUser(over: Partial<User> = {}): User {
  return {
    id: "00000000-0000-4000-8000-000000000001",
    email: "a@skyhaul.dev",
    role: "admin",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}
