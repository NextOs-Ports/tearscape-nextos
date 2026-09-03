#!/usr/bin/env python3
"""Unit tests for recipes/make_geometry_proof.read_geometry_proof."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile

PORT = Path(__file__).resolve().parent.parent.parent
SPEC = importlib.util.spec_from_file_location(
    "make_geometry_proof", PORT / "recipes/make_geometry_proof.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

TUPLE = {"run_id": "run-1", "generation": "0123456789abcdef", "port_id": "tearscape"}


def init_line(**overrides) -> dict:
    record = {
        "schema": "nx-geometry-proof/1", "kind": "init", "provider": "sdl2",
        "video_driver": "wayland", "app_id": "tearscape-nextos",
        "app_id_source": "SDL_APP_ID", "raw_fb_w": 1080, "raw_fb_h": 1920,
        "display_w": 1920, "display_h": 1080, "requested_w": 1920,
        "requested_h": 1080, "configured_w": 1920, "configured_h": 1080,
        "drawable_w": 1920, "drawable_h": 1080, "fullscreen": True,
        "configure_events": 1, "wait_ms": 42, "timed_out": False,
    }
    record.update(TUPLE)
    record.update(overrides)
    return record


def resize_line(w: int, h: int, notified: bool = True) -> dict:
    return {"schema": "nx-geometry-proof/1", "kind": "resize", "w": w, "h": h,
            "drawable_w": w, "drawable_h": h, "notified": notified}


def fbdev_line(w: int = 640, h: int = 480) -> dict:
    return init_line(
        provider="fbdev", video_driver="", app_id="", app_id_source="",
        raw_fb_w=w, raw_fb_h=h, display_w=w, display_h=h, requested_w=w,
        requested_h=h, configured_w=w, configured_h=h, drawable_w=w,
        drawable_h=h, configure_events=0, wait_ms=0)


def write(records: list[dict], raw: str | None = None) -> Path:
    handle, name = tempfile.mkstemp(suffix=".jsonl", dir=os.environ.get("TMPDIR"))
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        if raw is not None:
            stream.write(raw)
        for record in records:
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")
    return Path(name)


def expect_fail(records: list[dict], token: str, raw: str | None = None, **kwargs) -> None:
    path = write(records, raw)
    try:
        try:
            MODULE.read_geometry_proof(path, **kwargs)
        except MODULE.GeometryProofError as error:
            assert token in str(error), (token, str(error))
        else:
            raise AssertionError(f"accepted receipt that should fail on {token!r}")
    finally:
        path.unlink()


def expect_pass(records: list[dict], **kwargs) -> dict:
    path = write(records)
    try:
        return MODULE.read_geometry_proof(path, **kwargs)
    finally:
        path.unlink()


def main() -> None:
    # Passing cases.
    summary = expect_pass([init_line()], run_id="run-1", generation="0123456789abcdef")
    assert summary["provider"] == "sdl2"
    assert (summary["final_w"], summary["final_h"]) == (1920, 1080)
    assert summary["resizes"] == 0 and summary["resize_notified"] is None
    summary = expect_pass([init_line(), resize_line(1280, 720)])
    assert (summary["final_w"], summary["final_h"]) == (1280, 720)
    assert summary["resizes"] == 1 and summary["resize_notified"] is True
    summary = expect_pass([fbdev_line()])
    assert summary["provider"] == "fbdev"
    summary = expect_pass([init_line(video_driver="KMSDRM", raw_fb_w=1280, raw_fb_h=720,
                                     display_w=1280, display_h=720, requested_w=1280,
                                     requested_h=720, configured_w=1280, configured_h=720,
                                     drawable_w=1280, drawable_h=720, configure_events=0)])
    assert summary["configure_events"] == 0

    # Portrait/cropped: configured equals raw while the display differs.
    expect_fail([init_line(configured_w=1080, configured_h=1920,
                           drawable_w=1080, drawable_h=1920)], "portrait/cropped")
    # Configured differs from display with no later resize.
    expect_fail([init_line(configured_w=1600, configured_h=900,
                           drawable_w=1600, drawable_h=900)], "without a later resize")
    # Empty app id.
    expect_fail([init_line(app_id="")], "app_id is empty")
    # Timed out.
    expect_fail([init_line(timed_out=True, wait_ms=1500)], "timed_out")
    # Drawable differs from configured.
    expect_fail([init_line(drawable_w=1080, drawable_h=1920)], "differs from configured")
    # Not fullscreen.
    expect_fail([init_line(fullscreen=False)], "not fullscreen")
    # Tuple mismatch against the video proof.
    expect_fail([init_line()], "run_id", run_id="other", generation="0123456789abcdef")
    expect_fail([init_line()], "generation", run_id="run-1", generation="ffff")
    # Structural failures.
    expect_fail([], "empty")
    expect_fail([init_line(), init_line()], "exactly one init")
    expect_fail([resize_line(1, 1)], "exactly one init")
    expect_fail([init_line(), {"schema": "nx-geometry-proof/1", "kind": "other"}], "unknown kind")
    expect_fail([{**init_line(), "schema": "nope"}], "schema")
    expect_fail([init_line()], "not JSON", raw="{broken\n")
    bad = init_line()
    del bad["app_id_source"]
    expect_fail([bad], "lacks app_id_source")
    expect_fail([init_line(wait_ms="1")], "wait_ms is not a non-negative integer")
    expect_fail([init_line(fullscreen=1)], "fullscreen is not a boolean")
    expect_fail([init_line(), resize_line(0, 720)], "positive integer")
    expect_fail([init_line(), {**resize_line(1280, 720), "drawable_w": 1}], "differs from its drawable")
    expect_fail([init_line(provider="x11")], "unknown provider")
    # fbdev regression: any deviation from the raw panel is refused.
    expect_fail([fbdev_line(), resize_line(1, 1)], "cannot resize")
    expect_fail([{**fbdev_line(), "configured_w": 1}], "drawable")  # drawable != configured
    expect_fail([{**fbdev_line(), "display_w": 1}], "equal the raw framebuffer")
    expect_fail([fbdev_line(0, 0)], "configured size is empty")
    print("GEOMETRY PROOF UNIT: PASS")


if __name__ == "__main__":
    main()
