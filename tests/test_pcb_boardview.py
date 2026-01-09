import zlib
from pathlib import Path

from boardbrain.pcb_boardview import parse_pcb_zlib_container


def test_pcb_zlib_container_parse(tmp_path: Path):
    payload = b"NET=PPBUS_AON REF=TPU1\n"
    comp = zlib.compress(payload)
    data = b"\x00" * 128 + comp + b"\x00" * 64
    path = tmp_path / "test.pcb"
    path.write_bytes(data)
    nets, net_to_refs, meta = parse_pcb_zlib_container(str(path))
    assert "PPBUS_AON" in nets
    assert meta["streams_decompressed"] >= 1
    assert "PPBUS_AON" in net_to_refs
    refs = {i["refdes"] for i in net_to_refs["PPBUS_AON"]}
    assert "TPU1" in refs
