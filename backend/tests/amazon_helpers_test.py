import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "amazon_helpers", Path(__file__).parents[1] / "routes" / "amazon_helpers.py"
)
amazon_helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(amazon_helpers)


def test_canonical_color_prefers_variant_then_product_and_attributes():
    product = {"color": "Bej", "attributes": [{"name": "Renk", "value": "Siyah"}]}
    assert amazon_helpers.canonical_amazon_color(product, {"color": "Mürdüm"}) == "Mürdüm"
    assert amazon_helpers.canonical_amazon_color(product, {}) == "Bej"
    assert amazon_helpers.canonical_amazon_color(
        {"attributes": [{"name": "Renk", "value": "Acı Kahve"}]}, {}
    ) == "Acı Kahve"
    assert amazon_helpers.canonical_amazon_color({}, {}, "Siyah") == "Siyah"


def test_variant_images_are_first_deduplicated_and_limited_to_amazon_slots():
    product = {
        "main_image": "https://cdn.meluni.com.tr/product-main.webp",
        "images": [f"https://cdn.meluni.com.tr/p-{i}.webp" for i in range(12)],
    }
    variant = {
        "main_image": "https://cdn.meluni.com.tr/color-main.webp",
        "images": ["https://cdn.meluni.com.tr/color-main.webp",
                   "https://cdn.meluni.com.tr/color-side.webp"],
    }
    images = amazon_helpers.amazon_image_candidates(product, variant)
    assert images[:3] == [
        "https://cdn.meluni.com.tr/color-main.webp",
        "https://cdn.meluni.com.tr/color-side.webp",
        "https://cdn.meluni.com.tr/product-main.webp",
    ]
    assert len(images) == 9


def test_size_table_is_sent_to_amazon_after_product_images_but_video_is_not():
    product = {
        "images": [
            "https://cdn.meluni.com.tr/front.webp",
            {"url": "https://cdn.meluni.com.tr/size-chart.webp", "is_size_table": True},
            {"url": "https://cdn.meluni.com.tr/catwalk.mp4", "is_video": True},
            {"url": "https://cdn.meluni.com.tr/side.webp"},
        ]
    }
    assert amazon_helpers.amazon_image_candidates(product) == [
        "https://cdn.meluni.com.tr/front.webp",
        "https://cdn.meluni.com.tr/side.webp",
        "https://cdn.meluni.com.tr/size-chart.webp",
    ]


def test_seller_sku_uses_canonical_color_to_prevent_color_collision():
    variant = {"stock_code": "FC100", "size": "M", "barcode": "8691"}
    assert amazon_helpers.build_amazon_seller_sku(variant, {}, "Siyah") == "FC100-Siyah-M"
    assert amazon_helpers.build_amazon_seller_sku(variant, {}, "Acı Kahve") == "FC100-AcıKahve-M"


def test_seller_sku_is_safe_for_listings_api_path():
    variant = {"stock_code": "FC/100", "size": "M/L"}
    assert amazon_helpers.build_amazon_seller_sku(variant, {}, "Açık Bej") == "FC-100-AçıkBej-M-L"


def test_duplicate_color_size_records_resolve_to_same_seller_sku():
    variant = {"stock_code": "FCFW994865", "size": "M/L"}
    original = {"name": "Elen Yarasa Kol Basic Beyaz"}
    duplicate = {"name": "Elen Yarasa Kol Basic Beyaz (Kopya)"}
    assert amazon_helpers.build_amazon_seller_sku(variant, original, "Beyaz") == (
        amazon_helpers.build_amazon_seller_sku(variant, duplicate, "Beyaz")
    )


def test_existing_asin_sku_is_preferred_over_incomplete_generated_duplicate():
    rows = [
        {"sku": "FCSS1800001-Siyah-M", "asin": None, "amazon_status": "INCOMPLETE"},
        {"sku": "FCSS1800001-SIYAH-M", "asin": "B0HD21G9VY", "amazon_status": "ACTIVE"},
    ]
    picked = amazon_helpers.pick_existing_amazon_sku(rows, "FCSS1800001-Siyah-M")
    assert picked["sku"] == "FCSS1800001-SIYAH-M"


def test_generated_sku_wins_when_both_candidates_have_asin():
    rows = [
        {"sku": "OLD-SKU", "asin": "B000000001", "amazon_status": "ACTIVE"},
        {"sku": "NEW-SKU", "asin": "B000000002", "amazon_status": "BUYABLE"},
    ]
    picked = amazon_helpers.pick_existing_amazon_sku(rows, "NEW-SKU")
    assert picked["sku"] == "NEW-SKU"


def test_amazon_text_and_fabric_are_extracted_from_html_description():
    raw = "<p>Kumaş &amp; İçerik Bilgisi</p><p>Kumaş içeriği: %100 Pamuk<br>Rahat kullanım</p>"
    assert "<p>" not in amazon_helpers.clean_amazon_text(raw)
    assert "Kumaş & İçerik" in amazon_helpers.clean_amazon_text(raw)
    assert amazon_helpers.infer_amazon_fabric(raw) == "%100 Pamuk"


def test_sibling_query_covers_variant_and_numeric_card_stock_codes():
    query = amazon_helpers.amazon_sibling_query(["FC100", "12345"])
    clauses = query["$or"]
    assert {"variants.stock_code": {"$in": ["FC100", "12345"]}} in clauses
    assert {"variants.sku": {"$in": ["FC100", "12345"]}} in clauses
    assert {"urun_karti_id": {"$in": ["FC100", "12345", 12345]}} in clauses


def test_failure_detail_is_bounded_and_redacts_credentials():
    detail = amazon_helpers.safe_amazon_failure({
        "status": 400,
        "data": {"issues": [{"code": "90220", "message": "access_token=abc123 missing size"}]},
    })
    assert detail == {"code": "90220", "message": "access_token=[REDACTED] missing size", "http": 400}
    assert len(amazon_helpers.safe_amazon_failure(error="x" * 1000)["message"]) == 300


def test_retry_policy_escalates_then_quarantines():
    assert amazon_helpers.amazon_retry_policy(1) == (60, False)
    assert amazon_helpers.amazon_retry_policy(4) == (480, False)
    assert amazon_helpers.amazon_retry_policy(8) == (86400, True)
    assert amazon_helpers.amazon_retry_policy(50) == (86400, True)
