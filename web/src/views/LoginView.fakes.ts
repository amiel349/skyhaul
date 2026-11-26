// Fake db and endpoint for this view only: the user table behind POST /auth/login.
import { http, HttpResponse } from "msw";
import type { User } from "../api/types";
import { apiUrl, type Body, type Ok } from "../../tests/api";
import { makeUser } from "../../tests/factories/user";
import { registerReset } from "../../tests/reset";

interface Account {
  user: User;
  password: string;
}

export const fakeDb = {
  accounts: [] as Account[],
  /** Set to a number of seconds to make every login answer 429 with that Retry-After. */
  rateLimitedS: null as number | null,
  addUser(email: string, password: string): void {
    this.accounts.push({ user: makeUser({ email }), password });
  },
  reset(): void {
    this.accounts = [];
    this.rateLimitedS = null;
  },
};
registerReset(() => fakeDb.reset());

export const fakeEndpoints = [
  http.post(apiUrl("/auth/login"), async ({ request }) => {
    if (fakeDb.rateLimitedS !== null) {
      return new HttpResponse(null, { status: 429, headers: { "Retry-After": String(fakeDb.rateLimitedS) } });
    }
    const body = (await request.json()) as Body<"/auth/login", "post">;
    const account = fakeDb.accounts.find((a) => a.user.email === body.email && a.password === body.password);
    if (!account) return new HttpResponse(null, { status: 401 });
    return HttpResponse.json<Ok<"/auth/login", "post">>({
      access_token: "tok-1",
      token_type: "Bearer",
      expires_in: 900,
      user: account.user,
    });
  }),
];
