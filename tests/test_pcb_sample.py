import os
import pytest

from boardbrain.pcb_boardview import parse_pcb_zlib_container


@pytest.mark.parametrize(
    "path",
    [
        "/mnt/data/820-01955_820-01970.pcb",
        "kb_raw/iPhone/iPhone12_A2172/820-01955_820-01970/boardview/820-01955_820-01970.pcb",
    ],
)
def test_pcb_sample_file_parse(path: str):
    if not os.path.exists(path):
        pytest.skip(f"sample pcb not found: {path}")
    nets, net_to_refs, meta = parse_pcb_zlib_container(path)
    assert meta.get("parse_status") in ("success", "partial_success")
    assert len(nets) > 0
    assert meta.get("components_count", 0) > 0
    # If parse_status is success, expect mappings; for partial_success, allow zero.
    if meta.get("parse_status") == "success":
        assert meta.get("pairs_count", 0) > 0
