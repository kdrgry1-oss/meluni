"""Accessible, original and versioned storefront theme gallery."""
from copy import deepcopy
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from .deps import db, logger, require_admin, generate_id, require_permission
from tenant_config import get_tenant_config

admin_router = APIRouter(prefix="/admin/themes", tags=["Themes Admin"])
public_router = APIRouter(prefix="/storefront/themes", tags=["Storefront Themes"])

def _now(): return datetime.now(timezone.utc).isoformat()

THEME_TOKENS = {
    "atelier-editorial": {"background":"#F7F3ED","surface":"#FFFFFF","text":"#171512","muted":"#6F675E","accent":"#8B4A38","border":"#D8D0C5","font_heading":"Georgia, 'Times New Roman', serif","font_body":"Inter, ui-sans-serif, system-ui, sans-serif","radius":"2px"},
    "gallery-minimal": {"background":"#FFFFFF","surface":"#F3F4F1","text":"#10130F","muted":"#5D655B","accent":"#315C43","border":"#D9DED7","font_heading":"Inter, ui-sans-serif, system-ui, sans-serif","font_body":"Inter, ui-sans-serif, system-ui, sans-serif","radius":"14px"},
    "nocturne-studio": {"background":"#101114","surface":"#1A1C21","text":"#F7F3EA","muted":"#B9B5AD","accent":"#E8B86A","border":"#34363D","font_heading":"Georgia, 'Times New Roman', serif","font_body":"Inter, ui-sans-serif, system-ui, sans-serif","radius":"0px"},
}
THEME_META = {
    "atelier-editorial": ("Atelier Editorial", "Editöryel, görsel odaklı ve zarif vitrin.", "editorial", "https://images.unsplash.com/photo-1496747611176-843222e1e57c?auto=format&fit=crop&w=1800&q=82"),
    "gallery-minimal": ("Gallery Minimal", "Ferahlığı ve ürünü öne çıkaran modern galeri.", "minimal", "https://images.unsplash.com/photo-1529139574466-a303027c1d8b?auto=format&fit=crop&w=1800&q=82"),
    "nocturne-studio": ("Nocturne Studio", "Yüksek kontrastlı, sinematik ve güçlü sunum.", "nocturne", "https://images.unsplash.com/photo-1539109136881-3be0616acf4b?auto=format&fit=crop&w=1800&q=82"),
}

def _blocks(slug):
    name, _, _, image = THEME_META[slug]
    raw = [
        {"type":"announcement_bar","title":"Yeni koleksiyonu keşfedin","order":0,"settings":{"bg":THEME_TOKENS[slug]["text"],"color":THEME_TOKENS[slug]["background"]}},
        {"type":"hero_fullscreen","title":{"atelier-editorial":"Yeni sezon, zamansız çizgiler","gallery-minimal":"Günün seçkisi","nocturne-studio":"Geceye yeni bir yorum"}[slug],"subtitle":"Editöryel seçki","image":image,"mobile_image":image,"link_url":f"/tema/{slug}/kategori/tumu","link_label":"Koleksiyonu keşfet","order":1,"settings":{"text_color":"#FFFFFF","align":"center","overlay":0.32}},
        {"type":"product_scroller","title":"Öne çıkan ürünler","link_url":f"/tema/{slug}/kategori/tumu","link_label":"Tümünü gör","order":2,"settings":{"category_slug":"","limit":10}},
        {"type":"newsletter","title":"Bültenimize katılın","subtitle":"Yeni koleksiyon ve duyurulardan haberdar olun.","link_label":"Kaydol","order":3,"settings":{"bg":THEME_TOKENS[slug]["surface"],"color":THEME_TOKENS[slug]["text"]}},
    ]
    for b in raw:
        b.setdefault("subtitle",""); b.setdefault("image",""); b.setdefault("mobile_image",""); b.setdefault("link_url",""); b.setdefault("link_label","")
        b["id"] = generate_id(); b["is_active"] = True
    return raw

def _definition(slug, active=False):
    name, description, layout, _ = THEME_META[slug]; now = _now()
    return {"id":generate_id(),"template_key":slug,"name":name,"slug":slug,"description":description,"preview_image":"","is_active":active,"is_default":slug=="atelier-editorial","blocks":_blocks(slug),"menu":[],"settings":{"layout":layout,"tokens":deepcopy(THEME_TOKENS[slug]),"scroll_to_explore":layout=="editorial"},"created_at":now,"updated_at":now}

def _clean(doc):
    if not doc: return None
    out=deepcopy(doc); out.pop("_id",None); return out

def _validate_settings(settings):
    out=deepcopy(settings or {}); allowed=set(next(iter(THEME_TOKENS.values())))
    out["tokens"]={k:str(v)[:160] for k,v in (out.get("tokens") or {}).items() if k in allowed}
    return out

def _meluni_blocks():
    """Meluni Editorial — sade, geniş nefes alanlı editoryal düzen."""
    return [
        {"type": "announcement_bar", "title": "ÜCRETSİZ KARGO · 500₺ ÜZERİ",
         "subtitle": "", "image": "", "mobile_image": "", "link_url": "",
         "link_label": "", "order": 0,
         "settings": {"bg": "#100f0c", "color": "#ffffff"}},
        {"type": "hero_fullscreen", "title": "Yeni Sezon",
         "subtitle": "Sade çizgiler, günlük zarafet",
         "image": "", "mobile_image": "",
         "link_url": "/tema/meluni/kategori/elbise", "link_label": "Keşfet",
         "order": 1,
         "settings": {"text_color": "#ffffff", "align": "left", "overlay": 0.28}},
        {"type": "product_scroller", "title": "Öne Çıkanlar", "subtitle": "",
         "image": "", "mobile_image": "", "link_url": "", "link_label": "",
         "order": 2, "settings": {"limit": 12}},
        {"type": "editorial_card", "title": "Takım",
         "subtitle": "Tek parçada tamamlanmış görünüm",
         "image": "", "mobile_image": "",
         "link_url": "/tema/meluni/kategori/takim", "link_label": "İncele",
         "order": 3,
         "settings": {"text_color": "#ffffff", "align": "left", "overlay": 0.3}},
        {"type": "newsletter", "title": "Yeniliklerden ilk siz haberdar olun",
         "subtitle": "", "image": "", "mobile_image": "", "link_url": "",
         "link_label": "", "order": 4,
         "settings": {"bg": "#f4f3f1", "color": "#100f0c"}},
    ]


def _build_meluni_theme():
    """Meluni Editorial — yüklenen tasarımın renk ve tipografi belirteçleri.

    Belirteçler (`settings.tokens`) mağaza tarafında CSS değişkenine çevrilir
    (MiuMiuTheme.jsx). Tema kaydında yoksa eski Miu Miu değerleri geçerli olur,
    yani bu kayıt eklenmesi mevcut temayı ETKİLEMEZ.
    """
    jost = "'Jost', system-ui, -apple-system, sans-serif"
    return {
        "id": generate_id(),
        "name": "Meluni Editorial",
        "slug": "meluni",
        "description": ("Sıcak beyaz zemin, neredeyse siyah mürekkep, keskin "
                        "köşeler ve geniş harf aralığı. Başlıklar Jost."),
        "preview_image": "",
        "is_active": False,
        "is_default": False,
        "blocks": _meluni_blocks(),
        "menu": [],          # boş → mağaza gerçek kategori ağacını kullanır
        "settings": {
            "brand_name": "MELUNI",
            "scroll_to_explore": False,
            "font_url": ("https://fonts.googleapis.com/css2?"
                         "family=Jost:wght@300;400;500;600&"
                         "family=Bodoni+Moda:ital,wght@0,400;0,500;1,400&display=swap"),
            "tokens": {
                "ink": "#100f0c",
                "paper": "#ffffff",
                "line": "#e2e0db",
                "muted": "#8d887f",
                "accent": "#6c675f",
                "sans": jost,
                "display": jost,      # BAŞLIKLAR — yönetim isteği
                "wordmark": jost,
                "radius": "0px",
                "wordmarkTrack": "0.42em",
            },
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


async def _ensure_seed():
    legacy=await db.themes.find_one({"slug":"miumiu"},{"_id":0})
    if legacy:
        # Eski temanın bloklarını/ayarlarını bir GET çağrısında ezmek veri kaybıdır.
        # Yalnız müşteri-facing taklit adını nötrleştir; içerik ve aktiflik korunur.
        await db.themes.update_one({"id":legacy["id"]},{"$set":{
            "name":"Arşiv Tema", "description":"Önceki sürümden taşınan özel tema.",
            "legacy_source":"miumiu", "updated_at":_now(),
        }})
        logger.info("Neutralized legacy theme display metadata without replacing its content")
    for i,slug in enumerate(THEME_TOKENS):
        exists=await db.themes.find_one({"$or":[{"template_key":slug},{"slug":slug}]},{"_id":0,"id":1})
        if not exists:
            active=await db.themes.find_one({"is_active":True},{"_id":0,"id":1})
            await db.themes.insert_one(_definition(slug,i==0 and not active))
    active=await db.themes.find_one({"is_active":True},{"_id":0,"id":1})
    await db.theme_activation_state.update_one(
        {"id":"main"},{"$setOnInsert":{"id":"main","active_theme_id":(active or {}).get("id"),"updated_at":_now()}},upsert=True
    )

async def _version(theme,action,actor,metadata=None):
    v={"id":generate_id(),"theme_id":theme.get("id"),"theme_name":theme.get("name"),"action":action,"snapshot":_clean(theme),"metadata":metadata or {},"actor":actor.get("email") or actor.get("id") or "admin","created_at":_now()}
    await db.theme_versions.insert_one(deepcopy(v)); return v

@admin_router.get("")
async def list_themes(current_user:dict=Depends(require_permission("tasarim.view"))):
    await _ensure_seed(); items=await db.themes.find({},{"_id":0}).sort("created_at",1).to_list(50)
    return {"items":items,"total":len(items)}

@admin_router.get("/preview-data")
async def preview_data(current_user:dict=Depends(require_permission("tasarim.view"))):
    tenant=await get_tenant_config(db)
    cats=await db.categories.find({"is_active":{"$ne":False}},{"_id":0,"id":1,"name":1,"slug":1,"parent_id":1,"image":1}).sort("sort_order",1).to_list(8)
    products=await db.products.find({"is_active":{"$ne":False},"is_deleted":{"$ne":True}},{"_id":0,"id":1,"slug":1,"name":1,"title":1,"images":1,"image":1,"price":1,"sale_price":1,"category_id":1}).sort("updated_at",-1).to_list(8)
    return {"company":{"name":tenant["brand"].get("store_name") or "Mağaza","logo_url":tenant["brand"].get("logo_url") or ""},"categories":cats,"products":products}

@admin_router.get("/versions")
async def list_versions(limit:int=20,current_user:dict=Depends(require_permission("tasarim.view"))):
    items=await db.theme_versions.find({},{"_id":0,"snapshot":0}).sort("created_at",-1).to_list(min(max(limit,1),100)); return {"items":items}

@admin_router.post("/versions/{version_id}/rollback")
async def rollback_version(version_id:str,data:dict,current_user:dict=Depends(require_permission("tasarim.themes"))):
    if data.get("confirm") is not True: raise HTTPException(400,"Geri alma işlemi açık onay gerektirir")
    version=await db.theme_versions.find_one({"id":version_id},{"_id":0}); snapshot=(version or {}).get("snapshot")
    if not snapshot: raise HTTPException(404,"Tema sürümü bulunamadı")
    current=await db.themes.find_one({"id":snapshot.get("id")},{"_id":0})
    if current: await _version(current,"before_rollback",current_user,{"rollback_of":version_id})
    if snapshot.get("is_active"):
        state=await db.theme_activation_state.find_one({"id":"main"},{"_id":0,"active_theme_id":1})
        current_id=(state or {}).get("active_theme_id")
        cas=await db.theme_activation_state.update_one({"id":"main","active_theme_id":current_id},{"$set":{"active_theme_id":snapshot["id"],"updated_at":_now()}})
        if cas.modified_count != 1: raise HTTPException(409,"Aktif tema değişti; geçmişi yenileyip tekrar deneyin")
    await db.themes.update_one({"id":snapshot["id"]},{"$set":{**snapshot,"updated_at":_now()}},upsert=True)
    if snapshot.get("is_active"):
        await db.themes.update_one({"id":snapshot["id"]},{"$set":{"is_active":True}})
        await db.themes.update_many({"id":{"$ne":snapshot["id"]}},{"$set":{"is_active":False}})
    return {"ok":True,"theme":await db.themes.find_one({"id":snapshot["id"]},{"_id":0})}

@admin_router.get("/{theme_id}")
async def get_theme(theme_id:str,current_user:dict=Depends(require_permission("tasarim.view"))):
    theme=await db.themes.find_one({"id":theme_id},{"_id":0})
    if not theme: raise HTTPException(404,"Tema bulunamadı")
    return theme

@admin_router.post("")
async def create_theme(data:dict,current_user:dict=Depends(require_permission("tasarim.themes"))):
    slug=str(data.get("slug") or "yeni-tema").strip().lower()
    if await db.themes.find_one({"slug":slug},{"_id":0,"id":1}): raise HTTPException(400,"Tema adresi zaten kullanılıyor")
    now=_now(); theme={"id":generate_id(),"name":str(data.get("name") or "Yeni Tema")[:100],"slug":slug,"description":str(data.get("description") or "")[:500],"preview_image":data.get("preview_image") or "","is_active":False,"is_default":False,"blocks":data.get("blocks") or [],"menu":data.get("menu") or [],"settings":_validate_settings(data.get("settings")),"created_at":now,"updated_at":now}
    for b in theme["blocks"]: b.setdefault("id",generate_id()); b.setdefault("is_active",True)
    await db.themes.insert_one(deepcopy(theme)); return theme

@admin_router.put("/{theme_id}")
async def update_theme(theme_id:str,data:dict,current_user:dict=Depends(require_permission("tasarim.themes"))):
    theme=await db.themes.find_one({"id":theme_id},{"_id":0})
    if not theme: raise HTTPException(404,"Tema bulunamadı")
    await _version(theme,"before_customize",current_user); update={}
    for key in ("name","slug","description","preview_image","menu"):
        if key in data: update[key]=data[key]
    if "settings" in data: update["settings"]=_validate_settings(data["settings"])
    if "blocks" in data:
        update["blocks"]=data["blocks"] or []
        for b in update["blocks"]: b.setdefault("id",generate_id()); b.setdefault("is_active",True)
    if update.get("slug")!=theme.get("slug") and await db.themes.find_one({"slug":update.get("slug")},{"_id":0,"id":1}): raise HTTPException(400,"Tema adresi zaten kullanılıyor")
    update["updated_at"]=_now(); await db.themes.update_one({"id":theme_id},{"$set":update}); return await db.themes.find_one({"id":theme_id},{"_id":0})

@admin_router.put("/{theme_id}/blocks/{block_id}")
async def update_block(theme_id:str,block_id:str,data:dict,current_user:dict=Depends(require_permission("tasarim.themes"))):
    theme=await db.themes.find_one({"id":theme_id},{"_id":0})
    if not theme: raise HTTPException(404,"Tema bulunamadı")
    await _version(theme,"before_customize",current_user,{"block_id":block_id}); target=next((b for b in theme.get("blocks",[]) if b.get("id")==block_id),None)
    if not target: raise HTTPException(404,"Blok bulunamadı")
    for key in ("type","title","subtitle","image","mobile_image","link_url","link_label","order","is_active","settings"):
        if key in data: target[key]=data[key]
    await db.themes.update_one({"id":theme_id},{"$set":{"blocks":theme["blocks"],"updated_at":_now()}}); return {"ok":True,"blocks":theme["blocks"]}

@admin_router.post("/{theme_id}/activate")
async def activate_theme(theme_id:str,data:dict,current_user:dict=Depends(require_permission("tasarim.themes"))):
    if data.get("confirm") is not True: raise HTTPException(400,"Tema uygulama işlemi açık onay gerektirir")
    theme=await db.themes.find_one({"id":theme_id},{"_id":0})
    if not theme: raise HTTPException(404,"Tema bulunamadı")
    active=await db.themes.find_one({"is_active":True},{"_id":0}); expected=data.get("expected_current_theme_id")
    if expected!=(active or {}).get("id"): raise HTTPException(409,"Aktif tema değişti; listeyi yenileyip tekrar onaylayın")
    if active and active.get("id")==theme_id: return {"ok":True,"active_theme":theme_id,"already_active":True}
    version=await _version(active or theme,"before_activate",current_user,{"target_theme_id":theme_id})
    # A single compare-and-swap document serializes concurrent apply requests.
    cas=await db.theme_activation_state.update_one(
        {"id":"main","active_theme_id":expected},{"$set":{"active_theme_id":theme_id,"updated_at":_now()}}
    )
    if cas.modified_count != 1: raise HTTPException(409,"Aktif tema değişti; listeyi yenileyip tekrar onaylayın")
    await db.themes.update_one({"id":theme_id},{"$set":{"is_active":True,"updated_at":_now()}})
    await db.themes.update_many({"id":{"$ne":theme_id}},{"$set":{"is_active":False}})
    return {"ok":True,"active_theme":theme_id,"rollback_version_id":version["id"]}

@admin_router.delete("/{theme_id}")
async def delete_theme(theme_id:str,current_user:dict=Depends(require_permission("tasarim.themes"))):
    theme=await db.themes.find_one({"id":theme_id},{"_id":0})
    if not theme: raise HTTPException(404,"Tema bulunamadı")
    if theme.get("is_default") or theme.get("is_active"): raise HTTPException(400,"Varsayılan veya aktif tema silinemez")
    await _version(theme,"before_delete",current_user); await db.themes.delete_one({"id":theme_id}); return {"ok":True}

@admin_router.post("/{theme_id}/reset")
async def reset_theme(theme_id:str,current_user:dict=Depends(require_permission("tasarim.themes"))):
    theme=await db.themes.find_one({"id":theme_id},{"_id":0}); key=theme.get("template_key") if theme else None
    if not theme: raise HTTPException(404,"Tema bulunamadı")
    if key not in THEME_TOKENS: raise HTTPException(400,"Bu tema için fabrika ayarı bulunmuyor")
    await _version(theme,"before_reset",current_user); defaults=_definition(key)
    await db.themes.update_one({"id":theme_id},{"$set":{"blocks":defaults["blocks"],"menu":defaults["menu"],"settings":defaults["settings"],"name":defaults["name"],"description":defaults["description"],"updated_at":_now()}})
    return await db.themes.find_one({"id":theme_id},{"_id":0})

def _public(theme):
    out=_clean(theme); out["blocks"]=sorted([b for b in out.get("blocks",[]) if b.get("is_active",True)],key=lambda b:b.get("order",0)); out.pop("created_at",None); return out

@public_router.get("/active")
async def get_active_theme():
    await _ensure_seed(); state=await db.theme_activation_state.find_one({"id":"main"},{"_id":0,"active_theme_id":1})
    theme=await db.themes.find_one({"id":(state or {}).get("active_theme_id")},{"_id":0}) if (state or {}).get("active_theme_id") else None
    # Denetim #29: rastgele (taslak) tema yedeği KALDIRILDI — yalnız etkin ya da varsayılan tema.
    theme=theme or await db.themes.find_one({"is_active":True},{"_id":0}) or await db.themes.find_one({"is_default":True},{"_id":0})
    if not theme: raise HTTPException(404,"Aktif tema yok")
    return _public(theme)

@public_router.get("/{slug}")
async def get_theme_by_slug(slug:str, request: Request):
    await _ensure_seed(); theme=await db.themes.find_one({"slug":slug},{"_id":0})
    if not theme: raise HTTPException(404,"Tema bulunamadı")
    # Denetim #29: etkin olmayan (taslak) tema yalnız personele (panel önizlemesi) gösterilir.
    if not theme.get("is_active"):
        from .deps import optional_admin
        if (await optional_admin(request)) is None:
            raise HTTPException(404,"Tema bulunamadı")
    return _public(theme)
