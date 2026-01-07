from __future__ import annotations
import sys
from boardbrain.netlist import load_netlist, enforce_net_guardrail, canonicalize_net_name


def main() -> int:
    board_id = "820-02020"
    case = {"case_id": "TEST", "board_id": board_id, "model": "A2338"}
    nets, _ = load_netlist(board_id=board_id, model="A2338", case=case)
    if not nets:
        print("No netlist loaded; run ingest or ensure ./data/netlists/820-02020.json exists.")
        return 0

    text = "Measure PPBUS_G3H and PP3v3_S2 first."
    plan_items = [
        {"key": "PPBUS_G3H", "prompt": "Measure main rail"},
        {"key": "PP3v3_S2", "prompt": "Check 3V3"},
    ]

    sanitized_text, sanitized_items, report = enforce_net_guardrail(
        board_id=board_id,
        text=text,
        plan_items=plan_items,
        case=case,
    )

    if "PPBUS_G3H" in sanitized_text and "PPBUS_AON" in nets:
        print("FAIL: PPBUS_G3H was not corrected when PPBUS_AON exists")
        return 1
    if "PPBUS_AON" in nets and all(i["key"] != "PPBUS_AON" for i in sanitized_items):
        print("FAIL: PPBUS_G3H plan item not corrected to PPBUS_AON")
        return 1

    if "PP3V3_S2" in nets:
        if canonicalize_net_name("PP3v3_S2") not in nets:
            print("FAIL: canonicalization did not match PP3V3_S2")
            return 1

    if report.get("invalid_nets_detected"):
        print("Guardrail detected invalid nets:", report["invalid_nets_detected"])

    print("Net guardrail test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
