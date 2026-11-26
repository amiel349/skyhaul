// Fake session table behind POST /auth/refresh and /auth/logout. It stands for the httpOnly refresh
// cookie (which a test page can never see) and writes the readable skyhaul_csrf cookie like the server does.
import type { User } from "../../src/api/types";
import { registerReset } from "../reset";

const CSRF_COOKIE = "skyhaul_csrf";

function writeCsrfCookie(value: string | null): void {
  document.cookie = value === null ? `${CSRF_COOKIE}=; Max-Age=0; Path=/` : `${CSRF_COOKIE}=${value}; Path=/`;
}

export const sessionsDb = {
  /** Who the refresh cookie belongs to; null means the browser has no valid cookie. */
  user: null as User | null,
  csrf: "",
  /** Tokens issued so far: the next access token is `tok-${issued + 1}`. */
  issued: 1,
  /** Every POST /auth/refresh received (recorded before it is answered), and the CSRF header it carried. */
  refreshRequests: [] as Array<{ csrf: string | null }>,
  /** Every POST /auth/logout received. */
  logoutRequests: [] as Array<{ csrf: string | null }>,
  /** Start a session, as a login or an earlier visit would have: a valid cookie and its CSRF cookie. */
  start(user: User, csrf = "c".repeat(43)): void {
    this.user = user;
    this.csrf = csrf;
    writeCsrfCookie(csrf);
  },
  /** Rotate the CSRF token and hand out the next access token. */
  rotate(): { accessToken: string; csrf: string } {
    this.issued += 1;
    this.csrf = `rot-${this.issued}`.padEnd(43, "r");
    writeCsrfCookie(this.csrf);
    return { accessToken: `tok-${this.issued}`, csrf: this.csrf };
  },
  end(): void {
    this.user = null;
    writeCsrfCookie(null);
  },
  reset(): void {
    this.end();
    this.csrf = "";
    this.issued = 1;
    this.refreshRequests = [];
    this.logoutRequests = [];
  },
};
registerReset(() => sessionsDb.reset());
