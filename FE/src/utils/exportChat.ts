import { stripCitationLinks } from "./citations";

export interface ExportMessage {
  sender: "user" | "assistant";
  text: string;
}

const TITLE_WIDTH = 60;

/** The conversation's name: its first question, cut short. */
function chatTitle(messages: ExportMessage[]): string {
  const first = messages.find((m) => m.sender === "user")?.text.trim().replace(/\s+/g, " ") ?? "";
  if (!first) return "Chat";
  return first.length > TITLE_WIDTH ? `${first.slice(0, TITLE_WIDTH - 1)}…` : first;
}

const speaker = (m: ExportMessage, userName?: string | null) =>
  m.sender === "user" ? userName || "You" : "Assistant";

// Citation links carry signed download URLs that expire, so an export keeps
// only their labels.
const plain = (m: ExportMessage) =>
  (m.sender === "assistant" ? stripCitationLinks(m.text) : m.text).trim();

/** The conversation as text, for copying, sharing and saving. */
export function chatToText(messages: ExportMessage[], userName?: string | null): string {
  const body = messages.map((m) => `${speaker(m, userName)}:\n${plain(m)}`).join("\n\n");
  return `${chatTitle(messages)}\n${new Date().toLocaleString()}\n\n${body}\n`;
}

const escapeHtml = (s: string) =>
  s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

/** A filesystem-safe name for the saved file. */
function fileStem(messages: ExportMessage[]): string {
  const stem = chatTitle(messages).replace(/[\\/:*?"<>|…]+/g, "").replace(/\s+/g, "-");
  return stem || "chat";
}

/** Saves the conversation as a .txt file. */
export function downloadChatText(messages: ExportMessage[], userName?: string | null): void {
  const blob = new Blob([chatToText(messages, userName)], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${fileStem(messages)}.txt`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

const PRINT_STYLE = `
  body { font-family: -apple-system, "Segoe UI", Tahoma, Roboto, Arial, "Noto Sans Devanagari", sans-serif; color: #172554; margin: 0; padding: 24px; }
  h1 { font-size: 18px; margin: 0 0 4px; }
  .meta { color: #5b6b82; font-size: 12px; margin-bottom: 20px; }
  .msg { margin-bottom: 14px; break-inside: avoid; }
  .who { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; color: #5b6b82; margin-bottom: 3px; }
  .text { white-space: pre-wrap; word-break: break-word; font-size: 13px; line-height: 1.55; padding: 10px 12px; border-radius: 8px; }
  .user .text { background: #eceff3; }
  .assistant .text { background: #f7f9fc; border: 1px solid #dbe6f2; }
  @page { margin: 16mm; }
`;

/**
 * Opens the browser's print dialog on a formatted copy of the conversation;
 * "Save as PDF" there produces the file. Done in a hidden iframe rather than a
 * popup so a popup blocker cannot get in the way.
 */
export function printChatAsPdf(messages: ExportMessage[], userName?: string | null): void {
  const title = escapeHtml(chatTitle(messages));
  const rows = messages
    .map(
      (m) =>
        `<div class="msg ${m.sender}"><div class="who">${escapeHtml(speaker(m, userName))}</div>` +
        // dir="auto": an Arabic message is laid out right-to-left, an English one left-to-right.
        `<div class="text" dir="auto">${escapeHtml(plain(m))}</div></div>`,
    )
    .join("");
  const doc =
    `<!doctype html><html><head><meta charset="utf-8"><title>${title}</title>` +
    `<style>${PRINT_STYLE}</style></head><body>` +
    `<h1 dir="auto">${title}</h1><div class="meta">${escapeHtml(new Date().toLocaleString())}</div>${rows}</body></html>`;

  const frame = document.createElement("iframe");
  frame.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;";
  frame.srcdoc = doc;
  frame.onload = () => {
    const win = frame.contentWindow;
    if (!win) return frame.remove();
    win.onafterprint = () => frame.remove();
    win.focus();
    win.print();
  };
  document.body.appendChild(frame);
}
