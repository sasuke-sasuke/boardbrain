from __future__ import annotations
from boardbrain.measurement_parser import classify_and_parse


def run() -> int:
    known = {"PP3V3_S2", "PPBUS_AON"}

    cases = [
        ("When you say check if PP3V3_S2 is stable...", "QUESTION_ONLY", 0),
        ("PP3V3_S2 3.3V stable?", "MIXED", 1),
        ("PPBUS_AON r2g 12.5ohm", "MEASUREMENT_ONLY", 1),
        ("PP3V3_S2", "QUESTION_ONLY", 0),
    ]

    for text, expected_class, expected_count in cases:
        res = classify_and_parse(text, known)
        if res["classification"] != expected_class:
            print("FAIL classification", text, res["classification"], expected_class)
            return 1
        if len(res["entries"]) != expected_count:
            print("FAIL entries", text, len(res["entries"]), expected_count)
            return 1
        if expected_count == 0 and res.get("rejected"):
            if text.startswith("When you say"):
                if res["rejected"][0].get("reason") not in ("missing_unit", "net_only_mention"):
                    print("FAIL rejected reason", text, res["rejected"])
                    return 1

    print("measurement_parser_test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
