from boardbrain.plan_utils import parse_requested_measurements


def test_parse_requested_measurements_basic():
    text = """
REQUESTED MEASUREMENTS (WHAT I NEED FROM YOU)
- KEY: CHECK_PPBUS_AON | PROMPT: Measure PPBUS_AON to GND | TYPE: voltage | NET: PPBUS_AON | OPTIONAL HINT: Use C7090 pad
- KEY: CHECK_PP3V3_S2 | PROMPT: Check PP3V3_S2 voltage | TYPE: voltage | NET: PP3V3_S2
"""
    items, meta = parse_requested_measurements(text, known_nets={"PPBUS_AON", "PP3V3_S2"})
    assert len(items) == 2
    assert items[0]["key"] == "CHECK_PPBUS_AON"
    assert "prompt" in items[0]
    assert "meta" in items[0]
    assert meta["parse_failed"] is False


def test_parse_requested_measurements_inline():
    text = """
REQUESTED MEASUREMENTS
- KEY: PPBUS_AON | PROMPT: Measure main rail
"""
    items, meta = parse_requested_measurements(text, known_nets={"PPBUS_AON"})
    assert len(items) == 1
    assert items[0]["key"] == "CHECK_PPBUS_AON"
    assert meta["parse_failed"] is False


def test_parse_requested_measurements_with_net_fields():
    text = """
REQUESTED MEASUREMENTS
- KEY: CHECK_PPBUS_AON
  TYPE: voltage
  NET: PPBUS_AON
  PROMPT: Measure PPBUS_AON
"""
    items, meta = parse_requested_measurements(text, known_nets={"PPBUS_AON"})
    assert len(items) == 1
    assert meta["parse_failed"] is False


def test_parse_requested_measurements_denylist_key():
    text = """
REQUESTED MEASUREMENTS
- KEY: PROMPT
  PROMPT: This should be ignored
"""
    items, meta = parse_requested_measurements(text, known_nets={"PPBUS_AON"})
    assert items == []
    assert meta["parse_failed"] is True


def test_parse_requested_measurements_invalid_net():
    text = """
REQUESTED MEASUREMENTS
- KEY: CHECK_FAKE_NET
  PROMPT: Measure fake net
"""
    items, meta = parse_requested_measurements(text, known_nets={"PPBUS_AON"})
    assert items == []
    assert meta["parse_failed"] is True


def test_normalize_requested_items_check_prefix():
    items = [
        {
            "key": "CHECK_PP3V3_S2",
            "net": "CHECK_PP3V3_S2",
            "type": "voltage",
            "prompt": "Measure PP3V3_S2",
        }
    ]
    from boardbrain.plan_utils import normalize_requested_items
    cleaned, err = normalize_requested_items(items, known_nets={"PP3V3_S2"})
    assert err == ""
    assert cleaned[0]["meta"]["net"] == "PP3V3_S2"


def test_normalize_requested_items_net_from_key():
    items = [
        {
            "key": "CHECK_PP1V8_ALWAYS",
            "type": "voltage",
            "prompt": "Measure PP1V8_ALWAYS",
        }
    ]
    from boardbrain.plan_utils import normalize_requested_items
    cleaned, err = normalize_requested_items(items, known_nets={"PP1V8_ALWAYS"})
    assert err == ""
    assert cleaned[0]["meta"]["net"] == "PP1V8_ALWAYS"


def test_parse_requested_measurements_json_block():
    text = "STEPS"
    items, meta = parse_requested_measurements(text, known_nets={"PPBUS_AON"})
    assert items == []
    assert meta["parse_failed"] is True
