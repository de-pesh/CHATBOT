import { useCallback, useState } from "react";

const STORAGE_KEY = "chatbot.authToken";

function readStoredToken(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredToken(token: string | null) {
  try {
    if (token) localStorage.setItem(STORAGE_KEY, token);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage unavailable: the token just does not survive a reload */
  }
}

/**
 * The bearer token this widget talks to main-service with, for when nothing else supplies
 * one - a host app that already authenticated the user should pass its own token as the
 * `authToken` prop instead (see App.tsx/FloatingChat.tsx), in which case this hook's own
 * token and login flow are simply unused.
 *
 * `authRequired` only ever flips true in response to a real 401 from the server - see
 * `markAuthRequired`. With DISABLE_AUTH=True on the backend (the default), nothing ever
 * calls it, so the login prompt this drives never appears; it only shows up once the
 * backend actually starts requiring a token.
 */
export function useAuthToken() {
  const [token, setToken] = useState<string | null>(readStoredToken);
  const [authRequired, setAuthRequired] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [loggingIn, setLoggingIn] = useState(false);

  /** Call when a request the current token was attached to comes back 401. */
  const markAuthRequired = useCallback(() => {
    setToken(null);
    writeStoredToken(null);
    setAuthRequired(true);
  }, []);

  const login = useCallback(async (url: string, email: string, password: string) => {
    setLoggingIn(true);
    setLoginError(null);
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ username: email, password }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.detail || "Incorrect email or password.");
      }
      const data = await response.json();
      setToken(data.access_token);
      writeStoredToken(data.access_token);
      setAuthRequired(false);
      return true;
    } catch (err: any) {
      setLoginError(err?.message || "Could not sign in. Check your connection and try again.");
      return false;
    } finally {
      setLoggingIn(false);
    }
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    writeStoredToken(null);
  }, []);

  return { token, authRequired, markAuthRequired, login, loginError, loggingIn, logout };
}
