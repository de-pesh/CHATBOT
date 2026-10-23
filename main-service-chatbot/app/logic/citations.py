"""Citation markers: what they look like, how they render as links, how they are stripped again.

The prompt numbers every retrieved passage (see `prompts.build_sources`) and asks
the model to put [n] straight after the claim a passage supports. The model cites
by number, but the reader sees the document and page instead: "[SAMA, Pg: 5]",
the short name and the page, linked to the file when the backend can mint a URL
and plain text when it cannot. Nothing is appended after the answer.
"""
import html
import re
import textwrap

# "[2]" or "[1, 3]", with whatever whitespace precedes it so an unknown marker
# can be dropped without leaving a stray space before the full stop.
MARKER = re.compile(r"(\s*)\[(\d+(?:\s*,\s*\d+)*)\]")

# A "Cited:" list at the end. The prompt no longer asks for one, but a model
# that falls back to the old habit would otherwise show the citations twice.
LEGACY_CITED_BLOCK = re.compile(r"^[ \t]*\**Cited:.*\Z", re.IGNORECASE | re.MULTILINE | re.DOTALL)

# The anchors `_link` builds, and the trailing "Sources:" list.
ANCHOR = re.compile(r"<a target='_blank'[^>]*>([^<]*)</a>")
SOURCES_BLOCK = re.compile(r"\n*^Sources:\n\[\d+\].*\Z", re.MULTILINE | re.DOTALL)

_EXTENSION = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|txt|md|csv|html?|eml|msg|png|jpe?g|webp)$", re.IGNORECASE)


def _link(url: str, label: str, title: str, link_style: str) -> str:
    """The anchor the frontend recognises. Every interpolated value is escaped:
    file_name came from a user-controlled upload (routers/file.py just takes
    file.filename as given), so a name like `a' onmouseover='...` would
    otherwise break out of the attribute for every other user this same chunk
    gets cited to. The frontend matches this exact shape - keep them in step.
    """
    return (
        f"<a target='_blank' style='{link_style}' "
        f"title='{html.escape(title, quote=True)}' "
        f"href='{html.escape(url, quote=True)}'>{html.escape(label, quote=True)}</a>"
    )


def short_name(file_name: str, width: int) -> str:
    """A compact label for a document: no extension, tidy spacing, cut at a word.

    "UAE Information Assurance Regulation v1 1 pdf.pdf" -> "UAE Information Assurance...".
    Only a label - the full name is still on the link's hover text.
    """
    name = re.sub(r"[_\s]+", " ", file_name).strip()
    ext = _EXTENSION.search(name)
    if ext:
        name = name[: ext.start()]
        # Some names carry the extension twice ("... v1 1 pdf.pdf").
        name = re.sub(rf"[ ._-]{ext.group(1)}$", "", name, flags=re.IGNORECASE)
    name = name.strip(" .-") or file_name.strip()
    short = textwrap.shorten(name, width=width, placeholder="…")
    # A single very long word cannot be cut at a space; cut it where it stands.
    return short if short != "…" else name[: width - 1] + "…"


def url_key(source: dict) -> str:
    """What a source's download URL is filed under: its own stored file when it has one (each version of a
    document does), else its file name."""
    return source.get("stored_path") or source["file_name"]


def _marker_label(source: dict, name_width: int) -> str:
    """The text of an inline citation: "[SAMA, Pg: 5]", "[SAMA v2, Pg: 5]" for a named version, or "[SAMA]" with no page."""
    name = short_name(source["file_name"], name_width).replace("<", "").replace(">", "")
    if source.get("version"):
        name += " " + str(source["version"]).replace("<", "").replace(">", "")
    page = f", Pg: {source['page']}" if source.get("page") is not None else ""
    return f"[{name}{page}]"


def _describe(source: dict) -> str:
    """"<file name>, Page: <n>", or just the name for a document without pages."""
    name = source["file_name"] + (f" ({source['version']})" if source.get("version") else "")
    if source.get("page") is not None:
        return f"{name}, Page: {source['page']}"
    return name


def cited_url_keys(text: str, by_n: dict) -> list:
    """The download-URL keys (url_key) of the documents `text` cites, each once, in the order they are first cited.

    A marker whose number is not in `by_n` is one the model made up.
    """
    numbers = dict.fromkeys(
        n for match in MARKER.finditer(text) for n in map(int, match.group(2).split(",")) if n in by_n
    )
    return list(dict.fromkeys(url_key(by_n[n]) for n in numbers))


def render_markers(text: str, by_n: dict, urls: dict, link_style: str, label_name_width: int) -> str:
    """Rewrites every [n] as its document label, linked when `urls` has a URL for it.

    `by_n` maps a passage number to {file_name, page}, `urls` a url_key to its
    download URL or None. A marker that cited nothing real vanishes, and takes
    its space with it; repeated citations of one document within a marker collapse.
    `link_style` and `label_name_width` are Settings.CITATION_LINK_STYLE and
    CITATION_LABEL_NAME_WIDTH.
    """
    def replace(match: re.Match) -> str:
        markers = []
        for n in map(int, match.group(2).split(",")):
            if n not in by_n:
                continue
            label = _marker_label(by_n[n], label_name_width)
            url = urls[url_key(by_n[n])]
            markers.append(_link(url, label, _describe(by_n[n]), link_style) if url else label)
        return match.group(1) + "".join(dict.fromkeys(markers)) if markers else ""

    return MARKER.sub(replace, text)


def strip_citations(text: str) -> str:
    """Turns a linkified answer back into plain prose.

    The answer the browser shows is not what the model should be shown next
    turn. Each citation carries a signed download URL of several hundred
    characters, and the "Sources" list repeats them, so a few earlier answers
    can add thousands of tokens to the prompt for nothing - and the [n] numbers
    would mean nothing against the next turn's freshly numbered passages.
    """
    if not text:
        return text
    text = SOURCES_BLOCK.sub("", text)
    # A citation link goes, with the space before it; any other link keeps its text.
    text = re.sub(r"[ \t]*<a target='_blank'[^>]*>\[[^<]*\]</a>", "", text)
    text = ANCHOR.sub(lambda m: html.unescape(m.group(1)), text)
    text = re.sub(r"[ \t]*\[[^\[\]]*, Pg: \d+\]", "", text)  # a label with no link: "[SAMA, Pg: 5]"
    return re.sub(r"[ \t]*\[\d+\]", "", text).rstrip()
