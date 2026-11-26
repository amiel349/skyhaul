// Fake user table behind POST /auth/login.
import type { User } from "../../src/api/types";
import { registerReset } from "../reset";

interface Account {
  user: User;
  password: string;
}

export const usersDb = {
  accounts: [] as Account[],
  add(user: User, password: string): void {
    this.accounts.push({ user, password });
  },
  find(email: string, password: string): User | undefined {
    return this.accounts.find((a) => a.user.email === email && a.password === password)?.user;
  },
  reset(): void {
    this.accounts = [];
  },
};
registerReset(() => usersDb.reset());
