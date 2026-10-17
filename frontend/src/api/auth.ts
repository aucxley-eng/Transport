import axios from "axios";

import { ApiError, describeError, getJson } from "./client";
import type { TokenPair } from "./tokens";
import type { CurrentUser } from "./types";

/**
 * Authentication against the SimpleJWT endpoints.
 *
 * `POST /auth/token/` is DRF's stock `TokenObtainPairView`, whose username field
 * is derived from `User.USERNAME_FIELD`. That model field is `phone`
 * (apps/accounts/models.py:59), so the payload key is `phone` -- not the
 * `username` every SimpleJWT example shows.
 */

export async function login(
  phone: string,
  password: string,
): Promise<TokenPair> {
  try {
    const { data } = await axios.post<TokenPair>("/api/v1/auth/token/", {
      phone,
      password,
    });
    return data;
  } catch (error) {
    // The login call deliberately bypasses the shared `api` instance: a 401 here
    // means "wrong credentials", and routing it through the refresh interceptor
    // would have it try to refresh a session that was never established.
    throw new ApiError(
      describeError(error),
      axios.isAxiosError(error) ? (error.response?.status ?? null) : null,
    );
  }
}

export function fetchCurrentUser(): Promise<CurrentUser> {
  return getJson<CurrentUser>("/me/");
}
