// The exact citation link main-service-chatbot/app/helpers/citation_helper.py emits -
// single-quoted, fixed attributes, escaped text/href. Matching only this
// precise shape (rather than rendering the whole message as HTML) means the
// assistant's answer text is still always plain, auto-escaped React text -
// nothing the model echoes back (from the user's question, or from a
// malicious document in the knowledge base) can ever be interpreted as
// markup, only ever as a citation link that our own backend built.
// The title (file name and page, shown on hover) is optional: answers saved
// before inline citations existed have links without one.
export const CITATION_LINK =
  /<a target='_blank' style='color:#eed202'(?: title='([^']*)')? href='([^']*)'>([^<]*)<\/a>/g;

// A link labelled "[SAMA, Pg: 5]" is an inline citation marker, styled as a small pill; any
// other label (the model's own markdown links, or a citation's fallback plain-text form when
// the backend had no download URL for it) is ordinary link text.
export const CITATION_MARKER = /^\[.+\]$/;

/**
 * citation_helper.py builds this with Python's html.escape() - correct for a
 * real HTML parser, which is not what happens here. The regex above pulls
 * href/label out as plain substrings and hands them straight to React props
 * (href={...}) and text children ({...}), neither of which decodes entities.
 * Left undone, this breaks every citation link outright: an Azure SAS URL's
 * "&" between query parameters comes through as "&amp;sp=r&amp;sv=...", a
 * literal substring Azure's own parser rejects, not the separator it needs.
 * &amp; must be decoded last - decoding it first would turn a literal
 * "&amp;lt;" into "&lt;" and then, on the next replace, into "<".
 */
export function decodeHtmlEntities(s: string): string {
  return s
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'")
    .replace(/&amp;/g, "&");
}

/** An answer as plain text: each citation link reduced to its label, e.g. "[SAMA, Pg: 5]". */
export function stripCitationLinks(text: string): string {
  return text.replace(CITATION_LINK, (_m, _title, _href, label) => decodeHtmlEntities(label));
}

// Every ASCII character CommonMark gives special meaning to. Escaping all of them in
// attacker/document-controlled text (a citation's file name, page label) before handing it to a
// markdown parser (see citationLinksToMarkdown) means that text can only ever render as itself -
// an underscore in "Q1_2024_report.pdf" can never be read as an emphasis marker, a "[" can never
// be read as the start of a second, nested link.
const MARKDOWN_SPECIAL = /[\\`*_{}[\]()#+\-.!|>~]/g;

function escapeMarkdown(s: string): string {
  return s.replace(MARKDOWN_SPECIAL, "\\$&");
}

/**
 * Rewrites every backend-built citation <a> tag (see CITATION_LINK) into an equivalent markdown
 * link, so MarkdownMessage can render a whole answer - prose and citations together - with one
 * markdown parser instead of splitting the text around raw HTML. The href goes inside <...>
 * (CommonMark's angle-bracket link destination) rather than bare, so a query string's "&" or "="
 * can never be misread as closing the link early.
 */
export function citationLinksToMarkdown(text: string): string {
  CITATION_LINK.lastIndex = 0;
  return text.replace(CITATION_LINK, (_m, title, href, label) => {
    const decodedLabel = escapeMarkdown(decodeHtmlEntities(label));
    const decodedHref = decodeHtmlEntities(href).replace(/[<>]/g, "");
    if (!title) return `[${decodedLabel}](<${decodedHref}>)`;
    const decodedTitle = decodeHtmlEntities(title).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
    return `[${decodedLabel}](<${decodedHref}> "${decodedTitle}")`;
  });
}
