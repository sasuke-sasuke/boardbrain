from boardbrain.components import extract_refdes_tokens, parse_component_measurements


def test_extract_refdes_tokens():
    text = "U7000 connects to R5120 and C7012."
    counts = extract_refdes_tokens(text)
    assert counts.get("U") is None
    assert "U7000" in counts
    assert "R5120" in counts
    assert "C7012" in counts


def test_component_measurement_regex():
    text = "COMP Q3200.gate: 0.62V"
    entries = parse_component_measurements(text)
    assert len(entries) == 1
    assert entries[0]["refdes"] == "Q3200"


def test_parse_component_measurements():
    text = "COMP U7000.pin3: 1.8V"
    entries = parse_component_measurements(text)
    assert len(entries) == 1
    assert entries[0]["refdes"] == "U7000"
    assert entries[0]["loc"] == "PIN3"
