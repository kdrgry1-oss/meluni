#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TÜM TicimaxWeb siparişlerini (HER durum) 2026-06-05'e kadar çek → orders'a upsert.

- Kaynağı TicimaxWeb olan siparişlerin TAMAMI: başarılı, teslim, iade, iptal, kısmi iade…
- Durum otomatik doğru sayfaya yönlenir:
    iade durumları  → İade Talepleri (Site) sayfası
    cancelled       → İptaller sayfası
    diğerleri       → Siparişler listeleri
- 2026-06-05 sonrası YENİ sistemin verisi olduğu için ÇEKİLMEZ (gap kapatılır, üzerine yazılmaz).
- Kaldığı yerden devam eder (state dosyası), idempotent (ticimax_order_id ile upsert).
- İlerlemeyi TUM_SIPARIS_ILERLEME.md'ye canlı yazar.

Çalıştırma:  repo kökünde (backend/ yanında):  python3 tum_siparis_cek.py
             ya da:  python3 tum_siparis_cek.py 'mongodb+srv://...'
Bitince TUM_SIPARIS_ILERLEME.md'de TAMAMLANDI görünür; script + md silinebilir.
"""
import json, os, sys, re, time
from datetime import datetime, timezone

try:
    from pymongo import MongoClient, UpdateOne
except ImportError:
    print("pymongo eksik → pip3 install pymongo"); sys.exit(1)

HERE = os.path.dirname(os.path.abspath(__file__))
for cand in (os.path.join(HERE, "backend"), HERE, os.path.join(HERE, "..", "backend")):
    if os.path.exists(os.path.join(cand, "ticimax_order_parser.py")):
        sys.path.insert(0, cand); break
try:
    from ticimax_order_parser import parse_ticimax_order
    from ticimax_client import set_domain
    from zeep import Client, Settings
    from zeep.transports import Transport
    from zeep.helpers import serialize_object
    from zeep.exceptions import Fault
except Exception as e:
    print(f"Bağımlılık/parser bulunamadı: {e}\nRepo kökünde (backend/ yanında) çalıştır. (pip3 install zeep)"); sys.exit(1)

MD    = os.path.join(HERE, "TUM_SIPARIS_ILERLEME.md")
STATE = os.path.join(HERE, "tum_siparis_state.json")
CUT   = "2026-06-05T23:59:59"          # bu tarihe (dahil) kadar
PAGE_SIZE = 100
SLEEP = float(os.environ.get("TICIMAX_SLEEP", "0.5"))

# Durum → hangi sayfada görünür (özet için)
SAYFA = {
    "return_requested":"İade","returned":"İade","refunded":"İade","partial_refunded":"İade",
    "return_approved":"İade","return_rejected":"İade","return_in_transit":"İade",
    "cancelled":"İptal",
}
def sayfa_of(st): return SAYFA.get(st, "Siparişler")

# ── Mongo ──
MONGO_URL = os.environ.get("MONGO_URL") or (sys.argv[1] if len(sys.argv) > 1 else None)
if not MONGO_URL:
    for p in ("backend/.env","../backend/.env",".env",os.path.join(HERE,"backend",".env")):
        if os.path.exists(p):
            for line in open(p,encoding="utf-8"):
                m=re.match(r'\s*MONGO_URL\s*=\s*["\']?([^"\'\n]+)',line)
                if m: MONGO_URL=m.group(1).strip(); break
        if MONGO_URL: break
if not MONGO_URL:
    print("MONGO_URL bulunamadı."); sys.exit(1)
client=MongoClient(MONGO_URL,serverSelectionTimeoutMS=15000); client.admin.command("ping")
db_name=os.environ.get("DB_NAME")
if not db_name:
    try: db_name=client.get_default_database().name
    except Exception:
        db_name=[d for d in client.list_database_names() if d not in ("admin","local","config")
                 and "orders" in client[d].list_collection_names()][0]
orders=client[db_name]["orders"]

# WS kodu + domain
st=orders.database["settings"].find_one({"id":"ticimax"}) or {}
api_key=st.get("api_key") or os.environ.get("TICIMAX_API_KEY") or "AKG0M8DTRSEBAIA898JA6HW22EDIU3"
domain="facette.ticimaxeticaret.com"
try: set_domain(domain)
except Exception: pass

soap=Client(f"https://{domain}/Servis/SiparisServis.svc?wsdl",
            settings=Settings(strict=False,xml_huge_tree=True),
            transport=Transport(timeout=120,operation_timeout=180))

def filtre():
    return {'EFaturaURL':-1,'EntegrasyonAktarildi':-1,'IptalEdilmisUrunler':False,'KampanyaGetir':True,
     'KargoEntegrasyonTakipDurumu':-1,'KargoFirmaID':-1,'OdemeDurumu':-1,'OdemeGetir':True,
     'OdemeTamamlandi':-1,'OdemeTipi':-1,'PaketlemeDurumu':-1,'PazaryeriIhracat':-1,
     'SiparisDurumu':-1,'SiparisID':-1,'SiparisKaynagi':'TicimaxWeb','SiparisKodu':'','SiparisNo':'',
     'StrPaketlemeDurumu':'','StrSiparisDurumu':'','StrSiparisID':'','TedarikciID':-1,
     'TeslimatMagazaID':-1,'UrunGetir':True,'UyeID':-1,'UyeTelefon':'','SiparisTarihiSon':CUT}

# ── State (resume) ──
state={"page":0,"yeni":0,"guncel":0,"islenen":0,"durumlar":{},"sayfalar":{}}
if os.path.exists(STATE):
    try: state=json.load(open(STATE,encoding="utf-8"))
    except Exception: pass

def kaydet_state(): json.dump(state,open(STATE,"w",encoding="utf-8"),ensure_ascii=False)

def md_yaz(txt,mode="a"):
    with open(MD,mode,encoding="utf-8") as f: f.write(txt)

if not os.path.exists(MD) or state["page"]==0:
    md_yaz(f"""# Ticimax Tüm Sipariş Çekimi — İlerleme

Başlangıç: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  DB: `{db_name}`  |  Kapsam: ≤ 2026-06-05, kaynak = TicimaxWeb

Bu dosya script çalıştıkça güncellenir. Sonunda **TAMAMLANDI** görünce script + md + state silinebilir.

Yönlendirme: iade durumları → İade sayfası · cancelled → İptaller · diğerleri → Siparişler.

| Sayfa | İşlenen (kümülatif) | Yeni | Güncel | Son tarih |
|------:|--------------------:|-----:|-------:|-----------|
""","w" if state["page"]==0 else "a")

print(f"Bağlandı: db={db_name} | domain={domain} | başlangıç sayfa={state['page']}")

bos_sayfa=False
sayfa=state["page"]
while not bos_sayfa:
    f=filtre()
    s={'BaslangicIndex':sayfa,'KayitSayisi':PAGE_SIZE,'SiralamaDegeri':'ID','SiralamaYonu':'DESC'}
    try:
        batch=serialize_object(soap.service.SelectSiparis(UyeKodu=api_key,f=f,s=s)) or []
    except Fault as e:
        if 'bulunamadı' in str(e): batch=[]
        else:
            print(f"Sayfa {sayfa} FAULT: {e} — 15sn bekle, tekrar dene")
            time.sleep(15); continue
    except Exception as e:
        print(f"Sayfa {sayfa} hata: {e} — 10sn bekle, tekrar dene")
        time.sleep(10); continue

    if not batch:
        bos_sayfa=True; break

    ops=[]; son_tarih=""
    for raw in batch:
        if str(raw.get("SiparisKaynagi") or "") != "TicimaxWeb":
            continue  # sadece site siparişleri
        doc=parse_ticimax_order(raw, api_key=api_key)
        if not doc: continue
        son_tarih=str(raw.get("SiparisTarihi") or "")[:10] or son_tarih
        stt=doc["status"]
        state["durumlar"][stt]=state["durumlar"].get(stt,0)+1
        sf=sayfa_of(stt); state["sayfalar"][sf]=state["sayfalar"].get(sf,0)+1
        set_ins={"id":__import__("uuid").uuid4().hex,"user_id":None,
                 "backfill_source":"ticimax_tum_2026-06-11"}
        ops.append(UpdateOne({"ticimax_order_id":doc["ticimax_order_id"]},
                             {"$set":doc,"$setOnInsert":set_ins},upsert=True))
    if ops:
        for i in range(0,len(ops),200):
            r=orders.bulk_write(ops[i:i+200],ordered=False)
            state["yeni"]+=r.upserted_count; state["guncel"]+=r.modified_count
    state["islenen"]+=len(ops)
    sayfa+=1; state["page"]=sayfa; kaydet_state()
    md_yaz(f"| {sayfa} | {state['islenen']} | {state['yeni']} | {state['guncel']} | {son_tarih} |\n")
    if sayfa % 10 == 0:
        print(f"  sayfa {sayfa} | işlenen {state['islenen']} | yeni {state['yeni']} güncel {state['guncel']} | {son_tarih}")
    if len(batch) < PAGE_SIZE:
        bos_sayfa=True
    time.sleep(SLEEP)

# Özet
md_yaz(f"\n**TAMAMLANDI** — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
md_yaz(f"Toplam işlenen TicimaxWeb sipariş: **{state['islenen']}** (yeni {state['yeni']}, güncel {state['guncel']})\n\n")
md_yaz("### Sayfa dağılımı\n")
for sf,n in sorted(state["sayfalar"].items(), key=lambda x:-x[1]):
    md_yaz(f"- {sf}: {n}\n")
md_yaz("\n### Durum dağılımı\n")
for stt,n in sorted(state["durumlar"].items(), key=lambda x:-x[1]):
    md_yaz(f"- {stt}: {n}\n")
md_yaz("\nScript + bu md + tum_siparis_state.json silinebilir.\n")

print(f"\nBİTTİ → işlenen {state['islenen']} (yeni {state['yeni']}, güncel {state['guncel']})")
print("Sayfa dağılımı:", state["sayfalar"])
print("Durum dağılımı:", state["durumlar"])
print(f"İlerleme: {MD}")
