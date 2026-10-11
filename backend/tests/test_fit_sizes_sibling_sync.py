"""'Uyduğu Bedenler' (standart beden) seçiminin renk kardeşlerine yansıma kuralı."""
from routes.products import _fit_sizes_to_push, _is_standard_sized


def test_standard_sized_detection_matches_panel_rule():
    assert _is_standard_sized([{"size": "STD"}, {"size": " standart "}])
    assert _is_standard_sized([{"size": "Tek  Beden"}, {"size": ""}])
    assert not _is_standard_sized([{"size": "STD"}, {"size": "M"}])
    assert not _is_standard_sized([])
    assert not _is_standard_sized([{"size": ""}])
    assert not _is_standard_sized(None)


def test_changed_selection_is_pushed_cleaned():
    assert _fit_sizes_to_push({"fit_sizes": [" s", "M", "m", "L"]}, {"fit_sizes": ["S"]}) == (["S", "M", "L"], True)
    assert _fit_sizes_to_push({"fit_sizes": ["S", "M"]}, {}) == (["S", "M"], True)


def test_unchanged_selection_is_marked_unchanged():
    # değişmeyen seçim yalnız BOŞ kardeşleri doldurur (çağıran taraf)
    assert _fit_sizes_to_push({"fit_sizes": ["s", "M"]}, {"fit_sizes": ["S", "M"]}) == (["S", "M"], False)


def test_missing_or_empty_selection_is_not_pushed():
    assert _fit_sizes_to_push({"name": "x"}, {"fit_sizes": ["S"]}) == (None, False)
    # boş seçim tüm gruba yayılmaz
    assert _fit_sizes_to_push({"fit_sizes": []}, {"fit_sizes": ["S"]}) == (None, False)
    assert _fit_sizes_to_push({"fit_sizes": None}, {}) == (None, False)
