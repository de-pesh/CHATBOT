/**
 * Minimal Server-Sent Events client for POST /chat/stream.
 *
 * The browser's built-in EventSource can only issue GET requests and cannot
 * send a body, so the chat payload is posted with fetch() and the SSE frames
 * are parsed off the response stream by hand.
 */

export interface ChatStreamHandlers {
  /** Fires once, first, with the conversation the turn was logged against. */
  onMeta?: (meta: { conversation_id: number }) => void;
  /** Pipeline progress: rephrasing | retrieving | generating | finalising. */
  onStage?: (stage: string) => void;
  /** The next chunk of the answer. Append it to what you already have. */
  onDelta?: (text: string) => void;
  /**
   * The finished turn. `response` is authoritative and should replace the text
   * accumulated from the deltas: the server may translate the answer after
   * generating it, and it turns citations into links.
   */
  onDone?: (data: { response: string; conversation_id: number }) => void;
  /** The turn failed. May arrive after some deltas have already been shown. */
  onError?: (detail: string) => void;
}

const STAGE_LABELS: Record<string, string> = {
  rephrasing: "Understanding your question...",
  retrieving: "Searching the knowledge base...",
  generating: "Writing the answer...",
  finalising: "Finishing up...",
};

export const stageLabel = (stage: string | null): string =>
  (stage && STAGE_LABELS[stage]) || "Bot is typing...";

/**
 * Posts `payload` and dispatches SSE events to `handlers`.
 *
 * Resolves `false` without calling any handler if the endpoint is unreachable
 * or unsupported (no streaming body, a 404 from an older backend), so the
 * caller can fall back to the blocking /chat endpoint. Anything that goes
 * wrong once the stream has started is reported through `onError` instead,
 * because by then the answer is already partly on screen.
 */
export async function streamChat(
  url: string,
  payload: unknown,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<boolean> {
  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(payload),
      signal,
    });
  } catch {
    return false;
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
        handlers.onError?.(data.detail ?? "The assistant could not answer.");
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
      handlers.onError?.("The connection to the assistant was interrupted.");
    }
  } finally {
    reader.releaseLock();
  }

  return true;
}
