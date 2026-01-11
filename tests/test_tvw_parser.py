import os
import pytest


def test_tvw_parser_smoke():
    path = "kb_raw/Lenovo/Model/NM-F031/boardview/NM-F031.tvw"
    if not os.path.exists(path):
        pytest.skip("TVW sample not present")
    from boardbrain.boardview.tvw_parser import parse_tvw

    nets, net_to_refs, meta = parse_tvw(path)
    assert meta.get("format") == "TVW_STRINGS"
    assert len(nets) > 100
    assert len(meta.get("components") or []) > 100
