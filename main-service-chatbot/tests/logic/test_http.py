"""Comma-separated settings, CORS origins, SSE frames and download headers."""
import json

from app.logic.http import (
    SSE_HEADERS,
    attachment_disposition,
    cors_origins,
    split_csv,
    sse_frame,
)


def test_a_comma_separated_setting_drops_blanks_and_trims():
    assert split_csv(" a , b,, c ,") == ["a", "b", "c"]
    assert split_csv("") == [] and split_csv(None) == []


def test_cors_origins_are_the_local_ones_then_the_deployments_own():
    assert cors_origins("http://a, http://b", "https://host.example") == ["http://a", "http://b", "https://host.example"]
    assert cors_origins("http://a", None) == ["http://a"]
    assert cors_origins("", "https://host.example") == ["https://host.example"]


def test_an_sse_frame_is_an_event_line_a_data_line_and_a_blank_line():
    frame = sse_frame("delta", {"text": "héllo"})
    assert frame == 'event: delta\ndata: {"text": "héllo"}\n\n'
    assert json.loads(frame.split("data: ")[1]) == {"text": "héllo"}


def test_the_stream_headers_stop_proxies_buffering_it():
    assert SSE_HEADERS["X-Accel-Buffering"] == "no"
    assert "no-transform" in SSE_HEADERS["Cache-Control"]


def test_a_download_is_offered_under_its_original_name_without_quotes():
    assert attachment_disposition('My "report".pdf') == 'attachment; filename="My report.pdf"'
