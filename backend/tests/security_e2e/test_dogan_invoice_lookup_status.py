"""Doğan e-Arşiv durum sorgusu: belge YOKKEN Doğan hata kodu değil STATUS=200 / "FATURA ID BULUNAMADI"
satırı döndürüyor (canlı, 2026-10-05). Bu kesin "yok" sayılmalı — aksi halde bekleyen fatura kaydı olan
sipariş sonsuza dek "DOĞRULANAMADI" diye takılı kalıyordu. Belge VARSA (numara dolu) "found" kalır;
numarasız ve tanımsız durum yine "unknown" (inceleme)."""
from types import SimpleNamespace

from routes.orders import _dogan_lookup_invoice_sync

U = "46795f20-2f9c-42c7-a76e-dd2b7b8664c8"


def _cli(earsiv_resp=None, efatura_resp=None):
    ea = SimpleNamespace(service=SimpleNamespace(GetEArchiveInvoiceStatus=lambda **k: earsiv_resp))
    ef = SimpleNamespace(service=SimpleNamespace(GetInvoice=lambda **k: efatura_resp))
    return SimpleNamespace(_get_earsiv_client=lambda: ea, _get_efatura_client=lambda: ef, _make_header=lambda: {})


def test_earsiv_status_fatura_id_bulunamadi_is_not_found():
    r = _dogan_lookup_invoice_sync(_cli({"INVOICE": [{"HEADER": {
        "UUID": U, "STATUS": "200", "STATUS_DESC": "FATURA ID BULUNAMADI", "INVOICE_ID": None}}]}), "e-arsiv", U)
    assert r["state"] == "not_found"


def test_earsiv_found_with_number_stays_found():
    r = _dogan_lookup_invoice_sync(_cli({"INVOICE": [{"HEADER": {
        "UUID": U, "STATUS": "130", "STATUS_DESC": "RAPORLANDI", "INVOICE_ID": "FCT2026000021752"}}]}), "e-arsiv", U)
    assert r["state"] == "found" and r["invoice_number"] == "FCT2026000021752"


def test_earsiv_unknown_status_without_number_stays_unknown():
    r = _dogan_lookup_invoice_sync(_cli({"INVOICE": [{"HEADER": {
        "UUID": U, "STATUS": "100", "STATUS_DESC": "KUYRUKTA", "INVOICE_ID": None}}]}), "e-arsiv", U)
    assert r["state"] == "unknown"                  # kuyrukta olabilir → yeni fatura KESİLMEZ


def test_efatura_status_not_found_without_number():
    r = _dogan_lookup_invoice_sync(_cli(efatura_resp={"INVOICE": [{"UUID": U, "ID": None, "HEADER": {
        "STATUS": "200", "STATUS_DESCRIPTION": "FATURA ID BULUNAMADI"}}]}), "e-fatura", U)
    assert r["state"] == "not_found"
