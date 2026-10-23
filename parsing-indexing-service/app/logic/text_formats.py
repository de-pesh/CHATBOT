"""Text utilities, image pre-processing and email parsers.

Everything here is a blocking, in-memory transform run via asyncio.to_thread.
PAGE_BREAK is the page delimiter that logic/chunking.py splits on; Docling's
markdown output does not use it, so heading-based chunking ignores it.
Format-specific document extractors (PDF, DOCX, PPTX, XLSX, HTML) moved to
parsing/extractors.py and are now handled by Docling.
"""
import base64
import codecs
import re
from collections.abc import Iterable, Sequence
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from io import BytesIO

from PIL import Image, ImageOps

_RULE = "-" * 50
PAGE_BREAK = f"\n\n{_RULE}\n\n"

UNNAMED_ATTACHMENT = "(unnamed)"

# How much of an .html file is searched for its <meta charset>.
_CHARSET_SNIFF_BYTES = 4096


def join_pages(pages: Iterable[str]) -> str:
    return "".join(f"\n{page}{PAGE_BREAK}" for page in pages)


def scanned_pages_text(pages: Iterable[str]) -> str:
    """Pages read off a scanned PDF, each closed by PAGE_BREAK. A page that could
    not be read is passed as "" and still gets its delimiter: chunking numbers
    pages by delimiter position, so dropping it would renumber every later page."""
    return "\n".join(f"{page}{PAGE_BREAK}" for page in pages)


# ---------------------------------------------------------------------------
# Plain text, PDF, spreadsheets
# ---------------------------------------------------------------------------
# Byte-order marks and the codec each one announces. UTF-32 LE begins with the
# UTF-16 LE mark, so the longer marks come first. "-sig" and the plain utf-16/32
# codecs strip the mark themselves.
_BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def decode_bytes(raw: bytes, declared: str = "") -> str:
    """Bytes to text, with no guessing beyond what the bytes prove.

    A byte-order mark is authoritative. Otherwise the declared charset (an HTML
    <meta>, say) is tried, then strict UTF-8 - which almost never accepts text that
    is not UTF-8 - then cp1252 (Windows "ANSI"), then latin-1, which accepts every
    byte and so ends the chain. There is deliberately no BOM-less UTF-16: half of
    all even-length cp1252 files would decode as CJK, so a UTF-16 file without a
    mark is read as the single-byte text it will look like."""
    encodings = [encoding for mark, encoding in _BOMS if raw.startswith(mark)][:1]
    encodings += [declared] if declared else []
    encodings += ["utf-8", "cp1252", "latin-1"]
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    raise AssertionError("latin-1 decodes every byte string")  # pragma: no cover


def decode_txt(raw: bytes) -> str:
    return decode_bytes(raw)


def has_text(text: str) -> bool:
    """Whether extracted text holds anything besides whitespace and PAGE_BREAK rules.
    A scan whose pages are all blank is nothing but rules: it has pages, not text."""
    return bool(text.replace(PAGE_BREAK, "").strip())


def blank_page_share(text: str) -> tuple[int, int]:
    """(blank pages, pages) of extracted text split on PAGE_BREAK; (0, 0) when it has no page structure.
    A page counts as blank when nothing but whitespace is left on it."""
    if PAGE_BREAK not in text:
        return 0, 0
    pages = text.split(PAGE_BREAK)
    if pages and not pages[-1].strip():
        pages = pages[:-1]  # the rule that closes the last page leaves an empty tail, not a page
    return sum(1 for page in pages if not page.strip()), len(pages)


# ---------------------------------------------------------------------------
# Images the vision model may not accept as-is
# ---------------------------------------------------------------------------
def fit(img: Image.Image, max_side: int) -> Image.Image:
    """`img` itself if its long side is within `max_side`, else a scaled-down copy."""
    if max(img.size) <= max_side:
        return img
    img = img.copy()
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    return img


def png_base64(img: Image.Image) -> str:
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


# Formats sent as uploaded (when nothing else needs doing), the media type each is
# labelled with, and the pixel modes the vision API takes in them. MPO is the
# multi-picture JPEG phones write; its first image is an ordinary JPEG, so its
# extra pictures do not count as animation.
_PASS_THROUGH = {
    "PNG": ("image/png", {"1", "L", "LA", "P", "RGB", "RGBA"}),
    "JPEG": ("image/jpeg", {"L", "RGB"}),
    "MPO": ("image/jpeg", {"L", "RGB"}),
}
_EXIF_ORIENTATION = 0x0112


_HIGH_BIT_DEPTH_MODES = {"I;16", "I;16L", "I;16B", "I;16N", "I", "F"}


def _eight_bit(img: Image.Image) -> Image.Image:
    """`img` with 16-bit (or wider) grey values scaled down to 8 bits. Converting
    such an image straight to RGB clips every value above 255, which turns a
    16-bit scan solid white."""
    if img.mode not in _HIGH_BIT_DEPTH_MODES:
        return img
    return img.convert("F").point(lambda v: v * (1 / 256)).convert("L")


def prepare_image(raw: bytes, max_side: int, quality: int) -> tuple[bytes, str]:
    """(image bytes, media type) ready for the vision model.

    * A phone photo carries its rotation in EXIF rather than in its pixels, and
      a sideways image is read badly - it is turned upright first.
    * A very large image is scaled down to `max_side`.
    * A PNG or JPEG that needs neither, in a mode the API takes and not animated,
      is returned exactly as uploaded: decoding and re-saving would cost CPU and, for
      a JPEG, quality. Only its header is read.
    * Everything else (WebP, CMYK, 16-bit, ...) is re-encoded as PNG, first frame
      only for animated files, or as JPEG at `quality` for a JPEG source. 16-bit
      grey is scaled down to 8 bits first rather than clipped.
    * Bytes that are not a readable image raise (PIL's error), pass-through included.
    """
    with Image.open(BytesIO(raw)) as img:
        original_format = img.format
        if original_format in _PASS_THROUGH:
            media_type, modes = _PASS_THROUGH[original_format]
            if (
                img.mode in modes
                and max(img.size) <= max_side
                and (original_format == "MPO" or getattr(img, "n_frames", 1) == 1)
                and img.getexif().get(_EXIF_ORIENTATION, 1) not in range(2, 9)
            ):
                # Decoded (not re-encoded) so a truncated or corrupt file fails here, clearly,
                # rather than as an opaque rejection from the vision API. It is small: it is
                # within max_side.
                img.load()
                return raw, media_type
        image = fit(_eight_bit(ImageOps.exif_transpose(img)), max_side)
        out = BytesIO()
        if original_format in ("JPEG", "MPO"):
            image.convert("RGB").save(out, format="JPEG", quality=quality)
            return out.getvalue(), "image/jpeg"
        image.convert("RGBA" if "A" in image.getbands() else "RGB").save(out, format="PNG")
        return out.getvalue(), "image/png"


# ---------------------------------------------------------------------------
# HTML and e-mail
# ---------------------------------------------------------------------------
class _HtmlText(HTMLParser):
    """Visible text of an HTML page: no scripts or styles, block elements on
    their own lines, table cells separated by tabs."""

    _SKIP = {"script", "style", "noscript", "head", "template", "svg"}
    _BLOCK = {
        "p", "div", "br", "li", "tr", "section", "article", "header", "footer", "table",
        "ul", "ol", "blockquote", "pre", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag in self._SKIP:
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append("\t")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _HtmlText()
    parser.feed(markup)
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"[ \xa0]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"(?m)^\t+", "", text)          # a table row starts at the margin
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    title = parser.title.strip()
    return f"{title}\n\n{text}".strip() if title else text


def html_bytes_to_text(raw: bytes) -> str:
    """An .html / .htm file: its own <meta charset> decides the decoding."""
    meta = re.search(rb"charset=[\"']?([\w-]+)", raw[:_CHARSET_SNIFF_BYTES], re.IGNORECASE)
    return html_to_text(decode_bytes(raw, meta.group(1).decode("ascii") if meta else ""))


def message_text(lines: list[str], body: str, attachments: Sequence[str]) -> str:
    """The shared shape of an e-mail: header lines, attachment names, body."""
    if attachments:
        lines = lines + ["Attachments: " + ", ".join(attachments)]
    return "\n".join(lines) + "\n\n" + body.strip()


def header_lines(pairs: Iterable[tuple[str, object]]) -> list[str]:
    """`Label: value` for every pair with a value."""
    return [f"{label}: {value}" for label, value in pairs if value]


def eml_to_text(raw: bytes) -> str:
    """Headers, body and attachment names of an .eml message. Attachments are
    listed, not read."""
    message = BytesParser(policy=policy.default).parsebytes(raw)
    lines = header_lines((name, message[name]) for name in ("From", "To", "Cc", "Date", "Subject"))
    body = ""
    part = message.get_body(preferencelist=("plain", "html"))
    if part is not None:
        content = part.get_content()
        body = html_to_text(content) if part.get_content_type() == "text/html" else content
    return message_text(lines, body, [a.get_filename() or UNNAMED_ATTACHMENT for a in message.iter_attachments()])


def msg_text(message) -> str:
    """Headers, body and attachment names of an Outlook message, read as attributes
    of an `extract_msg.Message` (the caller opens and closes it)."""
    lines = header_lines(
        (("From", message.sender), ("To", message.to), ("Cc", message.cc), ("Date", message.date), ("Subject", message.subject))
    )
    body = message.body or ""
    if not str(body).strip() and message.htmlBody:
        html = message.htmlBody
        body = html_to_text(html if isinstance(html, str) else decode_bytes(html))
    names = [
        getattr(a, "longFilename", None) or getattr(a, "shortFilename", None) or UNNAMED_ATTACHMENT
        for a in message.attachments
    ]
    return message_text(lines, str(body), names)


