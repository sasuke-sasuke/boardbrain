import os
import pytest

from boardbrain.boardview.brd_parser import parse_brd


def test_brd_parser_sample():
    path = "kb_raw/MacBook/A2179/820-01958/boardview/820-01958.brd"
    if not os.path.exists(path):
        pytest.skip("sample BRD file not found")
    nets, net_to_refs, meta = parse_brd(path)
    assert meta.get("format", "").startswith("BRD_")
    assert len(nets) > 0
    assert meta.get("components_count", 0) > 0
    assert meta.get("pairs_count", 0) > 0
