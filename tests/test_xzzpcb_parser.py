import os
import pytest

from boardbrain.boardview.xzzpcb_parser import parse_xzzpcb, verify_xzzpcb


def test_xzzpcb_parser_sample():
    path = "kb_raw/iPhone/iPhone12_A2172/820-01955_820-01970/boardview/820-01955_820-01970.pcb"
    if not os.path.exists(path):
        pytest.skip("sample XZZPCB file not found")
    if not os.environ.get("BOARDVIEW_XZZPCB_KEY"):
        pytest.skip("BOARDVIEW_XZZPCB_KEY not set")
    assert verify_xzzpcb(open(path, "rb").read())
    nets, net_to_refs, meta = parse_xzzpcb(path)
    assert meta.get("format") == "XZZPCB"
    assert len(nets) > 0
    assert meta.get("components_count", 0) > 0
    assert meta.get("pairs_count", 0) > 0
