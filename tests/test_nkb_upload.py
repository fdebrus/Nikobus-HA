"""Tests for the options-flow .nkb upload (validate + save as nikobus.nkb)."""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import MagicMock, patch

import pytest


def _flow(tmp_path):
    from custom_components.nikobus.config_flow import NikobusOptionsFlow

    f = NikobusOptionsFlow.__new__(NikobusOptionsFlow)
    f.hass = MagicMock()
    f.hass.config.path = lambda name: str(tmp_path / name)

    async def _aaej(fn, *a):
        return fn(*a)

    f.hass.async_add_executor_job = _aaej
    return f


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _uploaded(src_path):
    """Fake process_uploaded_file: a CM yielding the source path."""

    @contextlib.contextmanager
    def cm(_hass, _file_id):
        yield src_path

    return cm


def test_save_uploaded_nkb_valid_copies_to_canonical(tmp_path):
    src = tmp_path / "WhateverName.nkb"
    src.write_bytes(b"pretend-zip-bytes")
    flow = _flow(tmp_path)

    with patch("homeassistant.components.file_upload.process_uploaded_file",
               _uploaded(str(src))), \
         patch("custom_components.nikobus.nkbnames.parse_nkb",
               return_value=MagicMock()):
        _run(flow._save_uploaded_nkb("file-id"))

    dest = tmp_path / "nikobus.nkb"
    assert dest.exists()
    assert dest.read_bytes() == b"pretend-zip-bytes"  # saved verbatim


def test_save_uploaded_nkb_invalid_rejected_and_not_saved(tmp_path):
    from custom_components.nikobus.config_flow import _NkbUploadError

    src = tmp_path / "not-really.nkb"
    src.write_bytes(b"garbage")
    flow = _flow(tmp_path)

    with patch("homeassistant.components.file_upload.process_uploaded_file",
               _uploaded(str(src))), \
         patch("custom_components.nikobus.nkbnames.parse_nkb",
               side_effect=ValueError("not a .nkb")):
        with pytest.raises(_NkbUploadError) as ei:
            _run(flow._save_uploaded_nkb("file-id"))

    assert ei.value.key == "invalid_nkb"
    # the canonical file must NOT be written when validation fails
    assert not (tmp_path / "nikobus.nkb").exists()


# ---------------------------------------------------------------------------
# parse_nkb extraction hardening (crafted-.nkb defences)
# ---------------------------------------------------------------------------

def test_parse_nkb_rejects_oversized_mdb(tmp_path, monkeypatch):
    """A decompression-bomb .mdb is rejected by the byte cap, not extracted."""
    import zipfile
    from custom_components.nikobus import nkbnames

    monkeypatch.setattr(nkbnames, "_MAX_MDB_BYTES", 16)
    bomb = tmp_path / "bomb.nkb"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("__niko__.mdb", b"A" * 64)  # 64 decompressed > 16 cap

    with pytest.raises(ValueError, match="safety limit"):
        nkbnames.parse_nkb(str(bomb))


def test_parse_nkb_traversal_member_name_stays_in_tmp(tmp_path):
    """An absolute / `..` .mdb member name cannot redirect the parser read
    outside the temp dir — it's written under a fixed local name."""
    import zipfile
    from custom_components.nikobus import nkbnames

    evil = tmp_path / "evil.nkb"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../../../etc/passwd.mdb", b"not-a-real-mdb")

    captured = {}

    class _Sentinel(Exception):
        pass

    def _fake_parser(path):
        captured["path"] = path
        raise _Sentinel()

    with patch(
        "custom_components.nikobus.vendor.access_parser.AccessParser", _fake_parser
    ):
        with pytest.raises(_Sentinel):
            nkbnames.parse_nkb(str(evil))

    p = captured["path"]
    assert p.endswith("project.mdb")
    assert "etc/passwd" not in p
    assert ".." not in p
