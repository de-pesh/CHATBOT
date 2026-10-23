/**
 * Minimal Server-Sent Events client for POST /chat/stream.
 *
 * The browser's built-in EventSource can only issue GET requests and cannot
 * send a body, so the chat payload is posted with fetch() and the SSE frames
 * are parsed off the response stream by hand.
 */
import type { ChatStrings } from "../utils/chatText";

/** Milliseconds the server spent per stage of one turn (POST /chat/stream `done`). */
export interface ServerTimings {
  prep_ms?: number;
  rephrase_ms?: number;
  retrieve_ms?: number;
  rerank_ms?: number;
  first_token_ms?: number;
  generate_ms?: number;
  translate_ms?: number;
  linkify_ms?: number;
  save_ms?: number;
  pipeline_ms?: number;
  total_ms?: number;
}

export interface ChatStreamHandlers {
  /** Fires once, first, with the conversation the turn was logged against. */
  onMeta?: (meta: { conversation_id: number }) => void;
  /** Pipeline progress: rephrasing | retrieving | reranking | generating | finalising. */
  onStage?: (stage: string) => void;
  /** The next chunk of the answer. Append it to what you already have. */
  onDelta?: (text: string) => void;
  /**
   * The finished turn. `response` is authoritative and should replace the text
   * accumulated from the deltas: the server may translate the answer after
   * generating it, and it turns citations into links.
   */
  onDone?: (data: {
    response: string;
    conversation_id: number;
    /** The chat_history row this turn was logged as - what POST /chat/history/{id}/feedback rates. */
    history_id?: number;
    /** Server-side milliseconds per stage - see ServerTimings. */
    timings?: ServerTimings;
    /** Follow-up question suggestions from the server, based on user profile and history. */
    suggested_questions?: string[];
  }) => void;
  /**
   * The turn failed. May arrive after some deltas have already been shown.
   * `detail` is the server's own (English, technical) message when it sent one -
   * for the console, not the user - and `reason` says whether the server reported
   * the failure or the connection dropped, so the caller can word it in the
   * user's language.
   */
  onError?: (detail: string | undefined, reason: "failed" | "interrupted") => void;
  /** The request itself was rejected as unauthenticated (401) - no stream ever opened. */
  onUnauthorized?: () => void;
}

/** The progress line for `stage`, in the wording `strings` (see chatStrings) holds. */
export const stageLabel = (stage: string | null, strings: ChatStrings): string =>
  (stage && strings.stages[stage]) || strings.typing;

/**
 * Posts `payload` and dispatches SSE events to `handlers`.
 *
 * Resolves `false` without calling any handler if the endpoint is unreachable
 * or unsupported (no streaming body, a 404 from an older backend), so the
 * caller can fall back to the blocking /chat endpoint. Anything that goes
 * wrong once the stream has started is reported through `onError` instead,
 * because by then the answer is already partly on screen. A 401 is reported
 * through `onUnauthorized` and resolves `true` (handled) rather than `false`:
 * the blocking endpoint the caller would fall back to needs the same token
 * and would 401 too, so falling back here would just hide the real problem.
 */
export async function streamChat(
  url: string,
  payload: unknown,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
  extraHeaders?: Record<string, string>,
): Promise<boolean> {
  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...extraHeaders,
      },
      body: JSON.stringify(payload),
      signal,
    });
  } catch {
    return false;
  }

  if (response.status === 401) {
    handlers.onUnauthorized?.();
    return true;
  }

  if (!response.ok || !response.body) {
    return false;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const dispatch = (frame: string) => {
    let event = "message";
    const dataLines: string[] = [];

    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trim());
      }
    }
    if (!dataLines.length) return;

    let data: any;
    try {
      data = JSON.parse(dataLines.join("\n"));
    } catch {
      return;
    }

    switch (event) {
      case "meta":
        handlers.onMeta?.(data);
        break;
      case "stage":
        handlers.onStage?.(data.stage);
        break;
      case "delta":
        handlers.onDelta?.(data.text ?? "");
        break;
      case "done":
        handlers.onDone?.(data);
        break;
      case "error":
        handlers.onError?.(data.detail, "failed");
        break;
    }
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Frames are separated by a blank line. Anything after the last blank
      // line is a partial frame and stays in the buffer for the next read.
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        dispatch(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");
      }
    }
    if (buffer.trim()) dispatch(buffer);
  } catch (err: any) {
    if (err?.name !== "AbortError") {
      handlers.onError?.(undefined, "interrupted");
    }
  } finally {
    reader.releaseLock();
  }

  return true;
}
