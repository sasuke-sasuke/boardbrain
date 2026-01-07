import os
import json

os.environ.setdefault("OPENAI_API_KEY", "test")

from boardbrain.net_refs import build_net_refs_from_texts, get_measure_points


def test_build_net_refs_from_texts_scores():
    known_nets = {"PPBUS_AON", "P3V3S2_PWR_EN"}
    known_refdes = {"C1234", "TP1", "R10"}
    texts = [
        "PPBUS_AON C1234 TP1\nP3V3S2_PWR_EN\nR10",
    ]
    net_to_refdes, meta = build_net_refs_from_texts(texts, known_nets, known_refdes)
    assert meta["net_count"] == 2
    assert "PPBUS_AON" in net_to_refdes
    refs = set(net_to_refdes["PPBUS_AON"])
    assert "C1234" in refs
    assert "TP1" in refs


def test_get_measure_points_pref_order():
    data_dir = os.path.join(os.getcwd(), "data")
    netlists_dir = os.path.join(data_dir, "netlists")
    netrefs_dir = os.path.join(data_dir, "net_refs")
    os.makedirs(netlists_dir, exist_ok=True)
    os.makedirs(netrefs_dir, exist_ok=True)

    board_id = "TEST"
    with open(os.path.join(netlists_dir, f"{board_id}.json"), "w", encoding="utf-8") as f:
        json.dump({"nets": ["PPBUS_AON"], "meta": {"source": "test"}}, f)

    net_refs = {
        "PPBUS_AON": ["C1234", "TP1"]
    }
    with open(os.path.join(netrefs_dir, f"{board_id}.json"), "w", encoding="utf-8") as f:
        json.dump({"net_to_refdes": net_refs, "meta": {"source": "test"}}, f)

    points = get_measure_points(board_id, "PPBUS_AON", k=5)
    assert points[0] == "TP1"
    assert "C1234" in points
    assert get_measure_points(board_id, "NO_SUCH_NET", k=5) == []


def test_get_measure_points_dict_items():
    data_dir = os.path.join(os.getcwd(), "data")
    netlists_dir = os.path.join(data_dir, "netlists")
    netrefs_dir = os.path.join(data_dir, "net_refs")
    os.makedirs(netlists_dir, exist_ok=True)
    os.makedirs(netrefs_dir, exist_ok=True)

    board_id = "DICTTEST"
    with open(os.path.join(netlists_dir, f"{board_id}.json"), "w", encoding="utf-8") as f:
        json.dump({"nets": ["PPBUS_AON"], "meta": {"source": "test"}}, f)

    net_refs = {
        "PPBUS_AON": [
            {"refdes": "C12", "kind": "C"},
            {"refdes": "P1", "kind": "P"},
        ]
    }
    with open(os.path.join(netrefs_dir, f"{board_id}.json"), "w", encoding="utf-8") as f:
        json.dump({"net_to_refdes": net_refs, "meta": {"source": "test"}}, f)

    points = get_measure_points(board_id, "PPBUS_AON", k=5)
    assert points[0] == "P1"
    assert "C12" in points
