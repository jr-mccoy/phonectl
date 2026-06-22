from phonectl import ocr

# header row + two data rows (Tesseract TSV)
TSV = "\n".join([
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
    "5\t1\t1\t1\t1\t1\t44\t380\t120\t40\t96.5\tWi-Fi",
    "5\t1\t1\t1\t1\t2\t170\t380\t90\t40\t12.0\t???",
    "5\t1\t1\t1\t1\t3\t44\t440\t200\t40\t88.0\tConnected",
])


def test_parse_tsv_extracts_text_and_bounds():
    regions = ocr.parse_tsv(TSV)
    texts = [r["text"] for r in regions]
    assert "Wi-Fi" in texts and "Connected" in texts
    wifi = next(r for r in regions if r["text"] == "Wi-Fi")
    assert wifi["bounds"] == [44, 380, 164, 420]
    assert 0.0 <= wifi["confidence"] <= 1.0


def test_parse_tsv_filters_low_confidence():
    regions = ocr.parse_tsv(TSV, min_confidence=0.5)
    assert all(r["confidence"] >= 0.5 for r in regions)
    assert "???" not in [r["text"] for r in regions]


def test_parse_tsv_skips_empty_text_and_header():
    regions = ocr.parse_tsv(TSV)
    assert all(r["text"].strip() for r in regions)
