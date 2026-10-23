import base64
import codecs
import io

import pytest
from app.logic.text_formats import (
    PAGE_BREAK,
    decode_bytes,
    decode_txt,
    eml_to_text,
    fit,
    has_text,
    html_bytes_to_text,
    html_to_text,
    join_pages,
    message_text,
    png_base64,
    prepare_image,
    scanned_pages_text,
)
from PIL import Image


# ------------------------------------------------------------------ join_pages

def test_join_pages_puts_every_page_between_a_newline_and_the_page_break():
    assert join_pages(["a", "b"]) == f"\na{PAGE_BREAK}\nb{PAGE_BREAK}"
    assert join_pages([]) == ""


def test_page_break_is_the_delimiter_chunking_splits_on():
    assert PAGE_BREAK == "\n\n" + "-" * 50 + "\n\n"


# ---------------------------------------------------------------------- decode

def test_decode_bytes_tries_the_declared_charset_first():
    assert decode_bytes("café".encode("cp1252"), "cp1252") == "café"
    assert decode_bytes("é".encode("utf-16-le"), "utf-16-le") == "é"


def test_an_unknown_declared_charset_is_ignored():
    assert decode_bytes(b"plain", "no-such-charset") == "plain"


TEXT = "Café “quoted” — naïve"


@pytest.mark.parametrize("raw, expected", [
    (b"", ""),
    (b"plain ascii", "plain ascii"),
    (TEXT.encode("utf-8"), TEXT),
    (codecs.BOM_UTF8 + TEXT.encode("utf-8"), TEXT),
    (codecs.BOM_UTF16_LE + TEXT.encode("utf-16-le"), TEXT),
    (codecs.BOM_UTF16_BE + TEXT.encode("utf-16-be"), TEXT),
    (codecs.BOM_UTF32_LE + TEXT.encode("utf-32-le"), TEXT),
    (codecs.BOM_UTF32_BE + TEXT.encode("utf-32-be"), TEXT),
], ids=["empty", "ascii", "utf-8", "utf-8-bom", "utf-16-le-bom", "utf-16-be-bom", "utf-32-le-bom", "utf-32-be-bom"])
def test_decode_txt_honours_a_byte_order_mark_and_otherwise_reads_utf8(raw, expected):
    assert decode_txt(raw) == expected


def test_a_cp1252_file_is_text_whatever_its_length_not_utf16_mojibake():
    odd = "Café déjà vu!".encode("cp1252")
    even = "Café crème brûlée été!".encode("cp1252")
    assert len(odd) % 2 == 1 and len(even) % 2 == 0
    # utf-16 accepts any even-length input and would turn these bytes into CJK-looking text.
    assert decode_txt(even) == "Café crème brûlée été!"
    assert decode_txt(odd) == "Café déjà vu!"


def test_cp1252_punctuation_is_kept_and_bytes_it_lacks_fall_to_latin1():
    assert decode_txt(b"\x93x\x94") == "“x”"  # cp1252 curly quotes
    assert decode_txt(b"a\x81b") == "a\x81b"  # 0x81 is undefined in cp1252: latin-1


def test_a_bom_overrides_the_declared_charset_and_is_stripped():
    assert decode_bytes(codecs.BOM_UTF8 + b"hi", "cp1252") == "hi"
    assert decode_bytes(codecs.BOM_UTF16_LE + "hi".encode("utf-16-le"), "utf-8") == "hi"


def test_a_utf16_file_without_a_mark_is_not_guessed():
    # No heuristics: it is read as the single-byte text its bytes are.
    assert decode_txt("hi".encode("utf-16-le")) == "h\x00i\x00"


# -------------------------------------------------------------------- has_text

def test_has_text_ignores_whitespace_and_page_breaks():
    assert has_text("word") and has_text(f"\n{PAGE_BREAK}\nword{PAGE_BREAK}")
    assert not has_text("") and not has_text(" \n\t")
    assert not has_text(PAGE_BREAK * 3)
    assert not has_text(join_pages(["", "  ", "\n"]))
    assert not has_text(scanned_pages_text(["", ""]))


# ------------------------------------------------------------------------ html

def test_html_text_skips_scripts_and_keeps_the_title_and_tables():
    markup = (
        "<html><head><title> Report </title><style>p{}</style></head><body>"
        "<script>evil()</script><p>Hello&nbsp;world</p>"
        "<table><tr><td>a</td><td>b</td></tr></table><ul><li>one</li></ul></body></html>"
    )
    text = html_to_text(markup)
    assert text.startswith("Report\n\nHello world")
    assert "evil" not in text and "p{}" not in text
    assert "a\tb" in text and "one" in text


def test_html_bytes_honour_the_meta_charset():
    raw = "<meta charset='cp1252'><p>“hi”</p>".encode("cp1252")
    assert html_bytes_to_text(raw) == "“hi”"


# ------------------------------------------------------------------------- eml

def test_eml_plain_body_headers_and_attachments():
    raw = (
        b"From: a@example.com\nTo: b@example.com\nSubject: Hi\nMIME-Version: 1.0\n"
        b"Content-Type: multipart/mixed; boundary=XX\n\n"
        b"--XX\nContent-Type: text/plain\n\nBody here\n"
        b"--XX\nContent-Type: application/pdf\nContent-Disposition: attachment; filename=r.pdf\n\nJVBER\n--XX--\n"
    )
    text = eml_to_text(raw)
    assert text.startswith("From: a@example.com\nTo: b@example.com\nSubject: Hi\nAttachments: r.pdf\n\n")
    assert text.endswith("Body here")


def test_eml_with_only_an_html_body_is_converted_to_text():
    raw = b"Subject: S\nContent-Type: text/html\n\n<p>Body <b>here</b></p>\n"
    assert eml_to_text(raw) == "Subject: S\n\nBody here"


def test_message_text_layout():
    assert message_text(["From: x"], "  body \n", []) == "From: x\n\nbody"
    assert message_text(["From: x"], "b", ["a.pdf", "b.png"]) == "From: x\nAttachments: a.pdf, b.png\n\nb"


# ---------------------------------------------------------------------- images

def image_bytes(size, mode="RGB", fmt="PNG", **save_args):
    buffer = io.BytesIO()
    Image.new(mode, size, "red" if mode == "RGB" else (255, 0, 0, 128)).save(buffer, fmt, **save_args)
    return buffer.getvalue()


def test_fit_leaves_a_small_image_alone_and_scales_a_large_one_on_a_copy():
    small = Image.new("RGB", (100, 50))
    assert fit(small, 200) is small
    big = Image.new("RGB", (400, 100))
    scaled = fit(big, 200)
    assert scaled is not big and scaled.size == (200, 50)
    assert big.size == (400, 100)


def jpeg_bytes(size=(100, 40), mode="RGB", exif_orientation=None, **save_args):
    image = Image.new(mode, size, "blue")
    if exif_orientation:
        exif = image.getexif()
        exif[0x0112] = exif_orientation
        save_args["exif"] = exif
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", **save_args)
    return buffer.getvalue()


def opened(raw):
    return Image.open(io.BytesIO(raw))


def test_prepare_image_scales_the_long_side_down_to_max_side():
    raw, mime = prepare_image(image_bytes((4000, 1000)), 2048, 92)
    assert mime == "image/png"
    assert opened(raw).size == (2048, 512)


def test_prepare_image_scales_a_large_jpeg_down_and_keeps_it_a_jpeg():
    raw, mime = prepare_image(jpeg_bytes((3000, 1000)), 1000, 92)
    assert mime == "image/jpeg" and opened(raw).format == "JPEG" and opened(raw).size == (1000, 333)


def test_a_small_png_is_passed_through_untouched():
    original = image_bytes((100, 50))
    raw, mime = prepare_image(original, 2048, 92)
    assert raw is original and mime == "image/png"


def test_a_small_jpeg_is_passed_through_untouched_whatever_the_quality():
    original = jpeg_bytes(quality=95)
    for quality in (10, 95):
        raw, mime = prepare_image(original, 2048, quality)
        assert raw is original and mime == "image/jpeg"


def test_an_image_exactly_max_side_long_is_not_re_encoded():
    original = image_bytes((2048, 100))
    assert prepare_image(original, 2048, 92)[0] is original
    assert prepare_image(original, 2047, 92)[0] is not original


@pytest.mark.parametrize("mode, color", [("RGBA", (255, 0, 0, 128)), ("L", 7), ("P", 3)])
def test_a_png_in_a_mode_the_api_takes_is_passed_through_untouched(mode, color):
    buffer = io.BytesIO()
    Image.new(mode, (30, 30), color).save(buffer, "PNG")
    original = buffer.getvalue()
    assert prepare_image(original, 2048, 92) == (original, "image/png")


def test_prepare_image_re_encodes_the_jpeg_of_an_oversized_photo_at_the_given_quality():
    noisy = Image.effect_noise((300, 300), 80).convert("RGB")
    buffer = io.BytesIO()
    noisy.save(buffer, "JPEG", quality=95)
    low_raw, low_mime = prepare_image(buffer.getvalue(), 200, 10)
    high_raw, high_mime = prepare_image(buffer.getvalue(), 200, 95)
    assert low_mime == high_mime == "image/jpeg"
    assert len(low_raw) < len(high_raw)


def test_prepare_image_re_encodes_other_formats_as_png():
    for fmt in ("BMP", "WEBP"):
        raw, mime = prepare_image(image_bytes((60, 60), fmt=fmt), 2048, 92)
        assert mime == "image/png" and opened(raw).format == "PNG"


def test_a_downscaled_rgba_png_keeps_its_alpha():
    raw, _ = prepare_image(image_bytes((400, 400), mode="RGBA"), 100, 92)
    assert opened(raw).mode == "RGBA" and opened(raw).size == (100, 100)


def test_a_downscaled_palette_png_becomes_rgb():
    buffer = io.BytesIO()
    Image.new("P", (400, 400), 3).save(buffer, "PNG")
    raw, mime = prepare_image(buffer.getvalue(), 100, 92)
    assert mime == "image/png" and opened(raw).mode == "RGB" and opened(raw).size == (100, 100)


def test_a_cmyk_jpeg_is_re_encoded_as_an_rgb_jpeg():
    original = jpeg_bytes(mode="CMYK")
    assert opened(original).mode == "CMYK"
    raw, mime = prepare_image(original, 2048, 92)
    assert raw != original and mime == "image/jpeg" and opened(raw).mode == "RGB"


def sixteen_bit_png(size=(20, 20), value=40000):
    buffer = io.BytesIO()
    Image.new("I;16", size, value).save(buffer, "PNG")
    assert opened(buffer.getvalue()).mode == "I;16"
    return buffer.getvalue()


def test_a_16_bit_png_is_scaled_to_8_bits_not_clipped_to_white():
    raw, mime = prepare_image(sixteen_bit_png(value=40000), 2048, 92)
    assert mime == "image/png" and opened(raw).mode == "RGB"
    assert opened(raw).getpixel((0, 0)) == (156, 156, 156)  # 40000 / 256
    assert opened(prepare_image(sixteen_bit_png(value=0), 2048, 92)[0]).getpixel((0, 0)) == (0, 0, 0)


def test_a_large_16_bit_png_is_scaled_to_8_bits_and_downscaled():
    raw, _ = prepare_image(sixteen_bit_png((400, 200), 20000), 100, 92)
    assert opened(raw).size == (100, 50) and opened(raw).getpixel((10, 10)) == (78, 78, 78)


@pytest.mark.parametrize("mode", ["I", "F"])
def test_other_wide_grey_modes_are_scaled_to_8_bits_too(mode):
    buffer = io.BytesIO()
    Image.new(mode, (10, 10), 40000).save(buffer, "TIFF")
    raw, _ = prepare_image(buffer.getvalue(), 2048, 92)
    assert opened(raw).getpixel((0, 0)) == (156, 156, 156)


def test_a_truncated_png_or_jpeg_fails_instead_of_being_passed_through():
    noisy = Image.effect_noise((120, 120), 80).convert("RGB")
    for fmt in ("PNG", "JPEG"):
        buffer = io.BytesIO()
        noisy.save(buffer, fmt)
        whole = buffer.getvalue()
        assert prepare_image(whole, 2048, 92)[0] is whole
        with pytest.raises(OSError):
            prepare_image(whole[: len(whole) // 2], 2048, 92)


def test_bytes_that_are_not_an_image_fail():
    with pytest.raises(OSError):
        prepare_image(b"not an image at all", 2048, 92)


def test_an_animated_png_is_reduced_to_its_first_frame():
    frames = [Image.new("RGB", (20, 20), color) for color in ("red", "green")]
    buffer = io.BytesIO()
    frames[0].save(buffer, "PNG", save_all=True, append_images=frames[1:])
    original = buffer.getvalue()
    assert getattr(opened(original), "n_frames", 1) == 2
    raw, mime = prepare_image(original, 2048, 92)
    assert raw != original and mime == "image/png" and getattr(opened(raw), "n_frames", 1) == 1


@pytest.mark.parametrize("orientation, size", [(6, (40, 100)), (8, (40, 100)), (3, (100, 40)), (2, (100, 40))])
def test_prepare_image_turns_an_exif_rotated_photo_upright(orientation, size):
    original = jpeg_bytes(exif_orientation=orientation)
    raw, mime = prepare_image(original, 2048, 92)
    assert raw != original and mime == "image/jpeg" and opened(raw).size == size


def test_an_exif_orientation_of_1_or_an_invalid_one_needs_no_re_encode():
    for orientation in (1, 9):
        original = jpeg_bytes(exif_orientation=orientation)
        assert prepare_image(original, 2048, 92)[0] is original


def test_a_multi_picture_jpeg_is_passed_through_as_a_jpeg():
    first, second = Image.new("RGB", (30, 30), "red"), Image.new("RGB", (30, 30), "blue")
    buffer = io.BytesIO()
    first.save(buffer, "MPO", save_all=True, append_images=[second])
    original = buffer.getvalue()
    assert opened(original).format == "MPO"
    assert prepare_image(original, 2048, 92) == (original, "image/jpeg")
    raw, mime = prepare_image(original, 10, 92)  # oversized: re-encoded as a plain JPEG
    assert mime == "image/jpeg" and opened(raw).format == "JPEG" and opened(raw).size == (10, 10)


def test_png_base64_round_trips():
    image = Image.new("RGB", (8, 8), "green")
    decoded = Image.open(io.BytesIO(base64.b64decode(png_base64(image))))
    assert decoded.format == "PNG" and decoded.size == (8, 8)


# ---- scanned pages, msg, header lines

def test_scanned_pages_each_end_in_a_page_break_and_an_unreadable_page_keeps_its_slot():
    assert scanned_pages_text(["one", "", "three"]) == f"one{PAGE_BREAK}\n{PAGE_BREAK}\nthree{PAGE_BREAK}"


def test_header_lines_skip_empty_values():
    from app.logic.text_formats import header_lines
    assert header_lines([("From", "a@x"), ("Cc", ""), ("Subject", None), ("To", "b@x")]) == ["From: a@x", "To: b@x"]


def _msg(**fields):
    from types import SimpleNamespace
    base = dict(sender="a@x", to="b@x", cc=None, date=None, subject="Hi", body="plain body", htmlBody=None, attachments=[])
    return SimpleNamespace(**{**base, **fields})


def test_a_msg_message_is_headers_attachment_names_and_body():
    from types import SimpleNamespace

    from app.logic.text_formats import msg_text
    attachments = [SimpleNamespace(longFilename="report.pdf"), SimpleNamespace(longFilename=None, shortFilename="R~1.PDF"), SimpleNamespace()]
    text = msg_text(_msg(attachments=attachments))
    assert text == "From: a@x\nTo: b@x\nSubject: Hi\nAttachments: report.pdf, R~1.PDF, (unnamed)\n\nplain body"


def test_a_msg_with_no_plain_body_falls_back_to_its_html_whether_str_or_bytes():
    from app.logic.text_formats import msg_text
    assert msg_text(_msg(body="  ", htmlBody="<p>from html</p>")).endswith("from html")
    assert msg_text(_msg(body=None, htmlBody=b"<p>from bytes</p>")).endswith("from bytes")


def test_blank_page_share_counts_empty_pages_and_ignores_the_closing_rule():
    from app.logic.text_formats import blank_page_share

    assert blank_page_share("no page structure") == (0, 0)
    assert blank_page_share(PAGE_BREAK.join(["a", " ", "b"])) == (1, 3)
    assert blank_page_share(f"a{PAGE_BREAK}b{PAGE_BREAK}") == (0, 2)
