import { useEffect, useState, lazy } from "react";
import { useParams } from "react-router-dom";
import axios from "axios";
import slugify from "../lib/slug";

const Category = lazy(() => import("../pages/Category"));
const ProductDetail = lazy(() => import("../pages/ProductDetail"));

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Bilinen kategori slug'ları — /categories çekilemese bile ana navigasyon ÇALIŞIR.
const STATIC_CAT_SLUGS = new Set([
  "giyim", "aksesuar", "en-yeniler", "sale", "tum-urunler", "tumu", "all",
  "ust-giyim", "alt-giyim", "dis-giyim",
  "elbise", "bluz", "kazak", "sweatshirt", "takim", "tisort", "gomlek",
  "etek", "pantolon", "sort", "jean",
  "kaban", "mont", "hirka", "trenckot", "ceket",
  "canta", "sal", "atki", "kemer", "sapka",
]);

// Dinamik kategori slug kümesi — bir kez çekilir, modül-cache'te tutulur.
let _catSlugsPromise = null;
function loadCategorySlugs() {
  if (!_catSlugsPromise) {
    _catSlugsPromise = axios
      .get(`${API}/categories?visible_only=true`)
      .then((res) => {
        const raw = res?.data;
        const cats = Array.isArray(raw) ? raw : raw?.categories || [];
        const set = new Set(STATIC_CAT_SLUGS);
        cats.forEach((c) => {
          if (c?.slug) set.add(String(c.slug).toLowerCase());
          if (c?.name) set.add(slugify(c.name));
        });
        return set;
      })
      .catch(() => new Set(STATIC_CAT_SLUGS)); // hata → statik küme (ürünler kırılmaz)
  }
  return _catSlugsPromise;
}

// /:slug → kategori mi ürün mü? Bilinen kategori slug'ları ANINDA Category olur;
// diğerleri varsayılan ÜRÜN gösterilir (hız), arka planda /categories ile teyit edilir;
// dinamik (admin'de açılmış) bir kategoriyse Category'ye geçer. Ürün sayfaları asla beklemez.
export default function SlugRouter() {
  const { slug } = useParams();
  const key = slugify(slug || "");
  const lower = (slug || "").toLowerCase();
  const staticCat = STATIC_CAT_SLUGS.has(key) || STATIC_CAT_SLUGS.has(lower);
  const [dynCat, setDynCat] = useState(false);

  useEffect(() => {
    setDynCat(false);
    if (staticCat) return;
    let alive = true;
    loadCategorySlugs().then((set) => {
      if (alive && (set.has(key) || set.has(lower))) setDynCat(true);
    });
    return () => {
      alive = false;
    };
  }, [slug, staticCat, key, lower]);

  return staticCat || dynCat ? <Category /> : <ProductDetail />;
}
