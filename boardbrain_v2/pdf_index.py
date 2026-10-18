"""Read explicit PAGE / CSA / CONTENTS tables without inferring circuit wiring."""
from __future__ import annotations


def sheet_index(doc) -> dict[int, dict]:
    """Use native word coordinates; plain extraction loses table row ordering.

    Only the explicitly labelled table layout is supported. Conflicting rows are
    discarded. PDF page positions remain authoritative for opening the original;
    a sheet number is an additional label sourced to the contents page.
    """
    entries, conflicts = {}, set()
    for toc_number in range(min(3, len(doc))):
        words = doc[toc_number].get_text("words")
        for anchor in words:
            if anchor[4] != "PAGE":
                continue
            y = (anchor[1] + anchor[3]) / 2
            tolerance = max(2, (anchor[3] - anchor[1]) * .25)
            headers = sorted((w for w in words if w[0] >= anchor[0] - 1
                              and abs((w[1] + w[3]) / 2 - y) < tolerance), key=lambda w: w[0])
            if [w[4] for w in headers[:3]] != ["PAGE", "CSA", "CONTENTS"]:
                continue
            stop = next((w[0] for w in headers[3:] if w[4] in ("SYNC", "DATE", "PAGE")), None)
            if stop is None:
                continue
            page_x, sheet_x, title_x = (headers[n][0] for n in range(3))
            candidates = [w for w in words if page_x - 2 <= w[0] < sheet_x - 2
                          and w[1] > anchor[3] and w[4].isdigit()]
            for number in candidates:
                page_number = int(number[4])
                if not 1 <= page_number <= len(doc):
                    continue
                row_y = (number[1] + number[3]) / 2
                row_tolerance = max(2, (number[3] - number[1]) * .25)
                row = sorted((w for w in words if sheet_x - 2 <= w[0] < stop - 2
                              and abs((w[1] + w[3]) / 2 - row_y) < row_tolerance), key=lambda w: w[0])
                sheet = [w[4] for w in row if w[0] < title_x - 2]
                title = " ".join(w[4] for w in row if w[0] >= title_x - 2)
                if len(sheet) != 1 or not sheet[0].isdigit():
                    continue
                entry = {"sheet": sheet[0], "page_title": title, "toc_page": toc_number + 1}
                if page_number in entries and entries[page_number] != entry:
                    conflicts.add(page_number)
                entries[page_number] = entry
    return {p: e for p, e in entries.items() if p not in conflicts}
