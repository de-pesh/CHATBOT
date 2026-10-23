import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CITATION_MARKER, citationLinksToMarkdown } from "../utils/citations";

// Only these link schemes are ever rendered as a real, clickable href - a "javascript:" or
// "data:" URL the model wrote (or echoed from a malicious document) is shown as inert text
// instead of something a click could execute.
const SAFE_URL = /^(https?:|mailto:)/i;

function textOf(node: React.ReactNode): string {
  if (typeof node === "string") return node;
  if (typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (React.isValidElement(node)) {
    return textOf((node.props as { children?: React.ReactNode })?.children);
  }
  return "";
}

/**
 * One link, styled one of two ways: a small pill for an inline citation marker like
 * "[SAMA, Pg: 5]" (see citations.ts: CITATION_MARKER), a plain hyperlink for anything else -
 * the same distinction Chatbot.tsx's old renderMessageText made, now driven by the rendered
 * link text instead of a regex over raw HTML.
 */
function MarkdownLink({ href, title, children }: { href?: string; title?: string; children?: React.ReactNode }) {
  const label = textOf(children);
  const isMarker = CITATION_MARKER.test(label);
  const safeHref = href && SAFE_URL.test(href) ? href : undefined;
  return (
    <a
      href={safeHref}
      title={title}
      target={safeHref ? "_blank" : undefined}
      rel={safeHref ? "noopener noreferrer" : undefined}
      style={
        isMarker
          ? {
              color: "#2c5f9e",
              backgroundColor: "#e4eefb",
              borderRadius: 4,
              padding: "0 4px",
              margin: "0 1px",
              fontSize: "0.75em",
              fontWeight: 600,
              verticalAlign: "baseline",
              lineHeight: 1,
              textDecoration: "none",
            }
          : { color: "#eed202" }
      }
    >
      {children}
    </a>
  );
}

const HEADING_EM = [1.2, 1.12, 1.05, 1, 0.95, 0.9];

function Heading({ level, children }: { level: number; children?: React.ReactNode }) {
  return (
    <div style={{ fontWeight: 700, fontSize: `${HEADING_EM[level - 1] ?? 0.9}em`, margin: "0.5em 0 0.25em" }}>
      {children}
    </div>
  );
}

// Compact block spacing so markdown structure (paragraphs, lists, headings, code, tables) reads
// well inside a small chat bubble instead of at article width.
const COMPONENTS = {
  a: MarkdownLink,
  p: ({ children }: { children?: React.ReactNode }) => <p style={{ margin: "0 0 0.5em" }}>{children}</p>,
  ul: ({ children }: { children?: React.ReactNode }) => (
    <ul style={{ margin: "0 0 0.5em", paddingInlineStart: "1.4em" }}>{children}</ul>
  ),
  ol: ({ children }: { children?: React.ReactNode }) => (
    <ol style={{ margin: "0 0 0.5em", paddingInlineStart: "1.4em" }}>{children}</ol>
  ),
  li: ({ children }: { children?: React.ReactNode }) => <li style={{ margin: "0.15em 0" }}>{children}</li>,
  h1: ({ children }: { children?: React.ReactNode }) => <Heading level={1}>{children}</Heading>,
  h2: ({ children }: { children?: React.ReactNode }) => <Heading level={2}>{children}</Heading>,
  h3: ({ children }: { children?: React.ReactNode }) => <Heading level={3}>{children}</Heading>,
  h4: ({ children }: { children?: React.ReactNode }) => <Heading level={4}>{children}</Heading>,
  h5: ({ children }: { children?: React.ReactNode }) => <Heading level={5}>{children}</Heading>,
  h6: ({ children }: { children?: React.ReactNode }) => <Heading level={6}>{children}</Heading>,
  blockquote: ({ children }: { children?: React.ReactNode }) => (
    <blockquote
      style={{ margin: "0.4em 0", padding: "0 0.8em", borderLeft: "3px solid rgba(23,37,84,0.15)", opacity: 0.85 }}
    >
      {children}
    </blockquote>
  ),
  hr: () => <hr style={{ border: "none", borderTop: "1px solid rgba(23,37,84,0.12)", margin: "0.6em 0" }} />,
  pre: ({ children }: { children?: React.ReactNode }) => (
    <pre
      style={{
        background: "rgba(23,37,84,0.06)", borderRadius: 8, padding: "10px 12px",
        overflowX: "auto", margin: "0.5em 0", fontSize: "0.85em", lineHeight: 1.5,
      }}
    >
      {children}
    </pre>
  ),
  code: ({ children }: { children?: React.ReactNode }) => (
    <code style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace", fontSize: "0.9em" }}>
      {children}
    </code>
  ),
  table: ({ children }: { children?: React.ReactNode }) => (
    <div style={{ overflowX: "auto", margin: "0.4em 0" }}>
      <table style={{ borderCollapse: "collapse", fontSize: "0.9em" }}>{children}</table>
    </div>
  ),
  th: ({ children }: { children?: React.ReactNode }) => (
    <th style={{ border: "1px solid rgba(23,37,84,0.15)", padding: "4px 8px", textAlign: "left" }}>{children}</th>
  ),
  td: ({ children }: { children?: React.ReactNode }) => (
    <td style={{ border: "1px solid rgba(23,37,84,0.15)", padding: "4px 8px" }}>{children}</td>
  ),
};

/**
 * An assistant answer, rendered as markdown (bold, lists, headings, tables, code) with its
 * backend-built citation links (see citations.ts: CITATION_LINK) folded in as ordinary markdown
 * links first, so the whole message goes through one parser instead of being split around raw
 * HTML. No raw HTML from the message itself is ever rendered (react-markdown does not interpret
 * it without the rehype-raw plugin, which this does not use) - a malicious document's text can
 * only ever come out as inert markdown-formatted prose, never as markup.
 */
export default function MarkdownMessage({ text }: { text: string }): React.ReactElement {
  const markdown = text.includes("<a ") ? citationLinksToMarkdown(text) : text;
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
      {markdown}
    </ReactMarkdown>
  );
}
