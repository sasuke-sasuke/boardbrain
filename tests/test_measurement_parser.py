from boardbrain.measurement_parser import classify_and_parse


def test_question_only_with_net():
    known = {"PP3V3_S2", "PPBUS_AON"}
    text = "When you say check if PP3V3_S2 is stable...?"
    res = classify_and_parse(text, known)
    assert res["classification"] == "QUESTION"
    assert res["entries"] == []


def test_mixed_question_and_measurement():
    known = {"PP3V3_S2", "PPBUS_AON"}
    text = "PP3V3_S2: 3.3V stable?"
    res = classify_and_parse(text, known)
    assert res["classification"] == "MIXED"
    assert len(res["entries"]) == 1


def test_measurement_only_r2g():
    known = {"PPBUS_AON"}
    text = "PPBUS_AON: r2g 12.5 ohm"
    res = classify_and_parse(text, known)
    assert res["classification"] == "MEASUREMENT"
    assert len(res["entries"]) == 1


def test_resistance_keyword_order():
    known = {"PPBUS_AON"}
    text = "PPBUS_AON: ohms 12.5"
    res = classify_and_parse(text, known)
    assert res["classification"] == "MEASUREMENT"
    assert len(res["entries"]) == 1


def test_net_mention_only():
    known = {"PP3V3_S2"}
    text = "PP3V3_S2"
    res = classify_and_parse(text, known)
    assert res["entries"] == []


def test_no_suffix_value_regression():
    known = {"PP3V3_S2"}
    text = "Why is PP3V3_S2 important?"
    res = classify_and_parse(text, known)
    assert res["entries"] == []


def test_question_with_unit_no_store():
    known = {"PP3V3_S2"}
    text = "Does it maintain 3.3v on PP3V3_S2?"
    res = classify_and_parse(text, known)
    assert res["classification"] == "QUESTION"
    assert res["entries"] == []


def test_no_measurement_without_explicit_kv():
    known = {"PP3V3_S2"}
    text = "PP3V3_S2 3.3V"
    res = classify_and_parse(text, known)
    assert res["classification"] == "MEASUREMENT"
    assert res["entries"] != []


def test_usb_c_port_reading():
    known = {"PPBUS_AON"}
    text = "USB-C: 5V 0.20A"
    res = classify_and_parse(text, known)
    assert res["classification"] == "MEASUREMENT"
    assert len(res["entries"]) == 1
    assert res["entries"][0]["net"] == "PORT:USBC"


def test_usb_c_mention_only():
    known = {"PPBUS_AON"}
    text = "USB-C is present"
    res = classify_and_parse(text, known)
    assert res["entries"] == []


def test_signal_net_with_explicit_kv():
    known = {"KBDBKLT_SW2"}
    text = "KBDBKLT_SW2: 1.2V"
    res = classify_and_parse(text, known)
    assert res["classification"] == "MEASUREMENT"
    assert len(res["entries"]) == 1


def test_signal_net_question_only():
    known = {"SMBUS_SCL"}
    text = "Should SMBUS_SCL be high?"
    res = classify_and_parse(text, known)
    assert res["classification"] == "QUESTION"
    assert res["entries"] == []
