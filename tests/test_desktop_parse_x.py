"""Unit tests for the desktop-eyes X-output parsers, against real captured
fixtures (no VM). [CROSS-REF] pyirix_qemu/desktop/parse_x.py."""
import os

import pytest

from pyirix_qemu.desktop import parse_x

FX = os.path.join(os.path.dirname(__file__), "..", "pyirix_qemu", "desktop",
                  "test_fixtures")


def _fx(name):
    p = os.path.join(FX, name)
    if not os.path.exists(p):
        pytest.skip(f"fixture missing: {p}")
    return open(p).read()


class TestParseXwininfoTree:
    def test_parses_desktop_tree(self):
        wins = parse_x.parse_xwininfo_tree(_fx("desktop_xwininfo_tree.txt"))
        assert len(wins) > 20, "should find many windows in the tree"
        by_id = {w["id"]: w for w in wins}
        # the Icon Catalog client window, captured live at 610x424+620+590
        cat = by_id.get("0x3400073")
        assert cat, "Icon Catalog client window not parsed"
        assert "Icon Catalog" in cat["name"]
        assert cat["class"] == "IconCatalog"
        assert (cat["w"], cat["h"], cat["x"], cat["y"]) == (610, 424, 620, 590)

    def test_frame_relationship(self):
        """The Icon Catalog client's 4Dwm frame is its root-child ancestor;
        the frame-vs-client delta must give the 32px titlebar / 8px border."""
        wins = parse_x.parse_xwininfo_tree(_fx("desktop_xwininfo_tree.txt"))
        by_id = {w["id"]: w for w in wins}
        cat = by_id["0x3400073"]
        # the frame is the topmost ancestor still in the parsed set (its parent
        # is the unparsed root window) -- works regardless of depth numbering.
        cur = cat
        while cur["parent"] in by_id:
            cur = by_id[cur["parent"]]
        assert cur["id"] == "0x1800078", "frame should be the root-child 0x1800078"
        titlebar = cat["y"] - cur["y"]
        border = cat["x"] - cur["x"]
        assert titlebar == 32, f"titlebar height {titlebar} != 32"
        assert border == 8, f"border {border} != 8"

    def test_no_name_and_no_class(self):
        wins = parse_x.parse_xwininfo_tree(_fx("desktop_xwininfo_tree.txt"))
        # plenty of frame/gadget windows have neither a name nor a class
        anon = [w for w in wins if not w["name"] and not w["class"]]
        assert anon, "expected anonymous gadget windows"


class TestParseXprop:
    def test_parses_wm_state(self):
        out = parse_x.parse_xprop_blocks(_fx("desktop_xprop_batch.txt"))
        cat = out.get("0x3400073")
        assert cat, "Icon Catalog xprop block not parsed"
        assert cat["managed"] is True
        assert cat["state"] == "normal"
        assert cat.get("class") == "IconCatalog"
        # the frame window has no WM_STATE -> unmanaged
        frame = out.get("0x1800078")
        if frame:
            assert frame["managed"] is False
