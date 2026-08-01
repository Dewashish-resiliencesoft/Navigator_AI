"""host_meta: OS fields + meeting label never keeps tokens."""

from __future__ import annotations

from navigator.logs.host_meta import capture_host_meta, meeting_label


def test_capture_host_meta_has_os_fields():
    m = capture_host_meta()
    assert m["host_os"]
    assert "host_release" in m
    assert "host_machine" in m
    assert "host_name" in m


def test_meeting_label_redacts_query_and_tokens():
    assert "pwd" not in meeting_label(
        "https://meet.google.com/haw-cyyt-ynv?authuser=1&pwd=SECRET"
    ).lower()
    assert "secret" not in meeting_label(
        "https://zoom.us/j/12345678901?zak=SECRETTOKEN"
    ).lower()
    assert meeting_label("https://meet.google.com/haw-cyyt-ynv").startswith("meet:")
    assert meeting_label(None) == ""
