from docex.pipeline.sources import locate_sources
from docex.providers.ocr import OcrLine


def line(text, x, y, w=300, h=20, words=None):
    return OcrLine(text, 0.9, (x, y, w, h), words or [])


def words(text, x, y, step=60, h=20):
    return [(t, x + i * step, y, step - 10, h) for i, t in enumerate(text.split())]


SIZE = (1000, 2000)


def test_amount_is_boxed_to_the_word_not_the_row():
    ln = line("Total   Rp 1.250.000", 100, 500, 400, 20, words("Total Rp 1.250.000", 100, 500))
    out = locate_sources({"total": 1250000.0}, [ln], SIZE)
    assert out["total"]["box"] == [0.22, 0.25, 0.05, 0.01]  # the third word only
    assert out["total"]["text"] == "Total   Rp 1.250.000"


def test_whole_line_when_the_engine_gives_no_words():
    ln = line("Total Rp 1.250.000", 100, 500, 400, 20)
    assert locate_sources({"total": 1250000.0}, [ln], SIZE)["total"]["box"] == [0.1, 0.25, 0.4, 0.01]


def test_ambiguous_amount_is_resolved_by_its_printed_label():
    lines = [line("Subtotal 1.000.000", 100, 100), line("Total 1.000.000", 100, 200)]
    out = locate_sources({"subtotal": 1000000.0, "total": 1000000.0}, lines, SIZE)
    assert out["subtotal"]["box"][1] == 0.05 and out["total"]["box"][1] == 0.1


def test_no_box_when_two_lines_fit_and_no_label_separates_them():
    lines = [line("Biaya 5.000", 100, 100), line("Lain 5.000", 100, 200)]
    assert locate_sources({"tax": 5000.0}, lines, SIZE) == {}


def test_date_in_another_notation_and_id_by_digits():
    lines = [line("Tanggal: 12 Januari 2025", 50, 80), line("NPWP : 01.234.567.8-901.000", 50, 160)]
    out = locate_sources({"date": "2025-01-12", "seller": {"npwp": "012345678901000"}}, lines, SIZE)
    assert set(out) == {"date", "seller.npwp"}


def test_name_must_match_closely_and_uniquely():
    lines = [line("CV Habibi Lazuardi Tbk", 50, 80), line("CV Habibi Lazuardi Tbk", 50, 400)]
    assert locate_sources({"vendor": {"name": "CV Habibi Lazuardi Tbk"}}, lines, SIZE) == {}
    assert "vendor.name" in locate_sources({"vendor": {"name": "CV Habibi Lazuardi Tbk"}}, lines[:1], SIZE)


def test_line_item_needs_description_and_total_on_the_same_row():
    lines = [line("Kabel NYM 3x2.5 50m   880.000", 50, 300), line("Oli Mesin 4L   223.000", 50, 340)]
    items = [{"description": "Kabel NYM 3x2.5 50m", "total": 880000.0}, {"description": "Oli Mesin 4L", "total": 999.0}]
    assert set(locate_sources({"line_items": items}, lines, SIZE)) == {"line_items.0"}


def test_empty_values_and_no_ocr_lines_give_nothing():
    assert locate_sources({"total": None, "date": ""}, [line("Total 1.000", 0, 0)], SIZE) == {}
    assert locate_sources({"total": 1000.0}, [], SIZE) == {}
