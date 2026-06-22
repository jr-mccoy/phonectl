"""Pure Tesseract-TSV -> region parsing. No I/O, no subprocess."""
from __future__ import annotations

import csv
import io


def parse_tsv(tsv: str, *, min_confidence: float = 0.0) -> list:
    regions = []
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t")
    for row in reader:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            conf = float(row.get("conf", "-1"))
        except ValueError:
            continue
        if conf < 0:
            continue
        confidence = conf / 100.0
        if confidence < min_confidence:
            continue
        left = int(row["left"]); top = int(row["top"])
        width = int(row["width"]); height = int(row["height"])
        regions.append({
            "text": text,
            "bounds": [left, top, left + width, top + height],
            "confidence": confidence,
        })
    return regions
