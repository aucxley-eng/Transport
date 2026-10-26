import axios, {
  AxiosError,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from "axios";

import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
  setTokens,
} from "./tokens";

/**
 * One axios instance for the whole app.
 *
 * Three things live here and nowhere else: the base path, the bearer header, and
 * the single-flight refresh. Scattering any of them is how you end up with a
 * screen that silently sends an expired token or races two refreshes against
 * each other and logs the user out on a token rotation.
 */

const API_BASE = "/api/v1";

export const api = axios.create({
  baseURL: API_BASE,
  headers: { "Content-Type": "application/json" },
});

/** Request config with our retry marker. */
interface RetryableConfig extends InternalAxiosRequestConfig {
  _retry?: boolean;
}

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

/**
 * In-flight refresh, shared by every 401 that arrives while it runs.
 *
 * Without this, a screen that fires six requests on mount produces six refresh
 * calls; SimpleJWT rotates the refresh token, so five of them present a token
 * that the first has already invalidated, and the user is ejected mid-session.
 */
let refreshInFlight: Promise<string> | null = null;

type SessionExpiredHandler = () => void;
let onSessionExpired: SessionExpiredHandler | null = null;

/** Register the callback fired when the session can no longer be refreshed. */
export function setSessionExpiredHandler(handler: SessionExpiredHandler | null) {
  onSessionExpired = handler;
}

async function refreshAccessToken(): Promise<string> {
  const refresh = getRefreshToken();
  if (!refresh) {
    throw new Error("No refresh token available.");
  }
  // A bare axios call, not `api`: going through the interceptors would attach
  // the dead access token and recurse into the refresh that is already failing.
  const { data } = await axios.post<{ access: string }>(
    `${API_BASE}/auth/token/refresh/`,
    { refresh },
  );
  // SimpleJWT returns only `access` here; the refresh token is unchanged.
  setTokens({ access: data.access, refresh });
  return data.access;
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const axiosErr = error as AxiosError; const config = axiosErr.config as RetryableConfig | undefined;
    const status = axiosErr.response?.status;
    const url = config?.url ?? "";

    // Only a genuine 401 is refreshable, and never the refresh endpoint itself.
    const isRefreshCall = url.includes("/auth/token/");
    if (status !== 401 || config === undefined || config._retry || isRefreshCall) {
      return Promise.reject(axiosErr);
    }

    config._retry = true;
    try {
      refreshInFlight ??= refreshAccessToken().finally(() => {
        refreshInFlight = null;
      });
      const access = await refreshInFlight;
      config.headers.Authorization = `Bearer ${access}`;
      return await api.request(config);
    } catch (refreshError) {
      // Only a rejection *by the server* means the refresh token is dead. A
      // network failure (wifi down, backend restarting) must not throw away a
      // valid 7-day session: keep the tokens and let the caller retry.
      const rejected =
        getRefreshToken() === null ||
        (axios.isAxiosError(refreshError) &&
          refreshError.response !== undefined);
      if (rejected) {
        clearTokens();
        onSessionExpired?.();
      }
      return Promise.reject(refreshError);
    }
  },
);

/** Thrown when the server rejects a request for a reason worth showing a user. */
export class ApiError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Turn any thrown value into a message fit to put in front of an operator.
 *
 * DRF puts field errors in an object and everything else in `detail`; both are
 * flattened here so a caller never has to render `[object Object]`.
 */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;

  if (error instanceof AxiosError) {
    const data = error.response?.data as unknown;

    if (typeof data === "string" && data.trim() !== "") return data;

    if (data && typeof data === "object") {
      const record = data as Record<string, unknown>;
      if (typeof record.detail === "string") return record.detail;

      const parts: string[] = [];
      for (const [field, value] of Object.entries(record)) {
        const text = Array.isArray(value) ? value.join(" ") : String(value);
        parts.push(field === "non_field_errors" ? text : `${field}: ${text}`);
      }
      if (parts.length > 0) return parts.join("\n");
    }

    if (error.response === undefined) {
      return "Could not reach the server. Check your connection and that the API is running.";
    }
    return `Request failed (${error.response.status}).`;
  }

  return error instanceof Error ? error.message : "Something went wrong.";
}

/** GET helper that unwraps `data` and normalises failures to ApiError. */
export async function getJson<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  try {
    const { data } = await api.get<T>(url, config);
    return data;
  } catch (error) {
    throw new ApiError(describeError(error), error instanceof AxiosError ? (error.response?.status ?? null) : null);
  }
}


/** POST helper that unwraps `data` and normalises failures to ApiError. */
export async function postJson<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  try {
    const { data } = await api.post<T>(url, body, config);
    return data;
  } catch (error) {
    throw new ApiError(describeError(error), error instanceof AxiosError ? (error.response?.status ?? null) : null);
  }
}

/** PATCH helper that unwraps `data` and normalises failures to ApiError. */
export async function patchJson<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  try {
    const { data } = await api.patch<T>(url, body, config);
    return data;
  } catch (error) {
    throw new ApiError(describeError(error), error instanceof AxiosError ? (error.response?.status ?? null) : null);
  }
}

/** DELETE helper that unwraps `data` and normalises failures to ApiError. */
export async function deleteJson<T = void>(url: string, config?: AxiosRequestConfig): Promise<T> {
  try {
    const { data } = await api.delete<T>(url, config);
    return data;
  } catch (error) {
    throw new ApiError(describeError(error), error instanceof AxiosError ? (error.response?.status ?? null) : null);
  }
}

export async function getList<T>(url: string, config?: AxiosRequestConfig): Promise<{ count: number; results: T[] }> {
  const { data } = await api.get<{ count: number; results: T[] }>(url, config);
  if (Array.isArray(data)) {
    return { count: data.length, results: data };
  }
  if (Array.isArray((data as any)?.results)) {
    return data as { count: number; results: T[] };
  }
  return { count: 0, results: [] };
}
