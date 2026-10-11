/** Original multi-layout storefront theme renderer. */
import React, { useEffect, useState } from "react";
import axios from "axios";
import { Link, useLocation, useParams } from "react-router-dom";
import { Search, User, Heart, ShoppingBag, X, Menu, ChevronDown } from "lucide-react";
import { themeCssVariables, themeImage, themePrice } from "../../lib/themeGallery";
import "./miumiu.css";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function StorefrontTheme() {
  const { slug } = useParams();
  const location = useLocation();
  const [theme, setTheme] = useState(null);
  const [loading, setLoading] = useState(true);
  const [mobileMenu, setMobileMenu] = useState(false);
  const [liveMenu, setLiveMenu] = useState([]);     // user's real categories → mega menu
  const [categories, setCategories] = useState([]);
  const [products, setProducts] = useState([]);
  const [company, setCompany] = useState({ site_name: "Meluni", logo_url: "" });

  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const url = slug ? `${API}/storefront/themes/${slug}` : `${API}/storefront/themes/active`;
        const [themeRes, catRes, productRes, companyRes] = await Promise.all([
          axios.get(url),
          axios.get(`${API}/categories?visible_only=true`).catch(() => ({ data: [] })),
          axios.get(`${API}/products`, { params: { limit: 60 } }).catch(() => ({ data: [] })),
          axios.get(`${API}/settings/maintenance`).catch(() => ({ data: {} })),
        ]);
        if (cancel) return;
        setTheme(themeRes.data);
        // Build mega menu from user's actual category tree
        const cats = Array.isArray(catRes.data) ? catRes.data : (catRes.data.items || []);
        const productItems = Array.isArray(productRes.data) ? productRes.data : (productRes.data.products || productRes.data.items || []);
        setCategories(cats);
        setProducts(productItems);
        setCompany(companyRes.data || {});
        setLiveMenu(buildMegaMenu(cats, themeRes.data.slug));
      } catch (e) {
        if (!cancel) setTheme(null);
      } finally {
        if (!cancel) setLoading(false);
      }
    })();
    return () => { cancel = true; };
  }, [slug]);

  if (loading) return <div className="mm-loading">Loading…</div>;
  if (!theme) return <div className="mm-loading">Tema bulunamadı.</div>;

  // Use live menu (from user's categories) — fallback to theme.menu only if no live data
  const menu = liveMenu.length > 0 ? liveMenu : (theme.menu || []);
  const blocks = theme.blocks || [];
  const announcement = blocks.find(b => b.type === "announcement_bar");
  const otherBlocks = blocks.filter(b => b.type !== "announcement_bar");
  const totalScreens = otherBlocks.filter(b => ["hero_fullscreen", "editorial_card"].includes(b.type)).length;

  const categoryMatch = location.pathname.match(/\/kategori\/([^/]+)/);
  const productMatch = location.pathname.match(/\/urun\/([^/]+)/);
  return (
    <div className="mm-root" data-layout={theme.settings?.layout || "editorial"} style={themeCssVariables(theme.settings?.tokens)}>
      {announcement && (
        <div className="mm-announcement" style={{ background: announcement.settings?.bg || "#000", color: announcement.settings?.color || "#fff" }}>
          {announcement.title}
        </div>
      )}

      <Header theme={theme} company={company} menu={menu} onToggleMobile={() => setMobileMenu(v => !v)} mobileOpen={mobileMenu} />
      {mobileMenu && <MobileMenu theme={theme} menu={menu} onClose={() => setMobileMenu(false)} />}

      <main>{productMatch ? <StoreProduct product={products.find(p => p.slug === decodeURIComponent(productMatch[1]))} /> : categoryMatch ? <StoreCategory slug={decodeURIComponent(categoryMatch[1])} categories={categories} products={products} themeSlug={theme.slug} /> : <>{otherBlocks.map((b, idx) => <BlockRenderer key={b.id} block={b} idx={idx} total={totalScreens} themeSlug={theme.slug} />)}<StoreCategory title="Koleksiyonlar" categories={categories} products={products.slice(0, 8)} themeSlug={theme.slug} compact /></>}</main>

      <Footer theme={theme} company={company} />
    </div>
  );
}

/**
 * Build a mega menu from the real category tree.
 * - Root categories (parent_id null) become top-level nav items.
 * - First-level children → grouped as columns within the mega panel.
 * - Second-level children → links inside each column.
 */
function buildMegaMenu(cats, themeSlug) {
  if (!cats || cats.length === 0) return [];
  const byParent = new Map();
  for (const c of cats) {
    const p = c.parent_id || "__root__";
    if (!byParent.has(p)) byParent.set(p, []);
    byParent.get(p).push(c);
  }
  const roots = (byParent.get("__root__") || []).filter(c => c.is_active !== false);
  // Sort by sort_order
  roots.sort((a, b) => (a.sort_order || 999) - (b.sort_order || 999));

  return roots.slice(0, 10).map(root => {
    const childrenL1 = (byParent.get(root.id) || []).filter(c => c.is_active !== false);
    const columns = [];
    if (childrenL1.length > 0) {
      // group L1 as columns — at most 4 columns
      for (const c1 of childrenL1.slice(0, 4)) {
        const childrenL2 = (byParent.get(c1.id) || []).filter(c => c.is_active !== false).slice(0, 10);
        columns.push({
          title: c1.name,
          links: [
            { label: `Tümü`, url: `/tema/${themeSlug}/kategori/${c1.slug}` },
            ...childrenL2.map(c2 => ({ label: c2.name, url: `/tema/${themeSlug}/kategori/${c2.slug}` })),
          ],
        });
      }
    }
    return {
      label: root.name.toUpperCase(),
      url: `/tema/${themeSlug}/kategori/${root.slug}`,
      columns,
    };
  });
}

/* ---------- Header ---------- */
function Header({ theme, company, menu, onToggleMobile, mobileOpen }) {
  const brand = company?.site_name || "Meluni";
  const [openIdx, setOpenIdx] = useState(null);

  return (
    <header className="mm-header" data-testid="storefront-header">
      <div className="mm-header-inner">
        <button className="mm-burger" onClick={onToggleMobile} aria-label="Menu" data-testid="btn-mobile-menu">
          {mobileOpen ? <X size={22}/> : <Menu size={22}/>}
        </button>
        <Link to={`/tema/${theme.slug}`} className="mm-logo" data-testid="brand-logo">{company?.logo_url ? <img src={company.logo_url} alt={brand}/> : brand}</Link>
        <nav className="mm-nav">
          {menu.map((m, i) => (
            <div key={i} className="mm-nav-item" onMouseEnter={() => setOpenIdx(i)} onMouseLeave={() => setOpenIdx(null)}>
              <a href={m.url} className={`mm-nav-link${m.accent ? ' mm-accent' : ''}`}>{m.label}</a>
              {openIdx === i && (m.columns || []).length > 0 && (
                <div className="mm-megamenu">
                  {(m.columns || []).map((col, j) => (
                    <div key={j} className="mm-mega-col">
                      {col.title && <h4>{col.title}</h4>}
                      <ul>
                        {(col.links || []).map((l, k) => (
                          <li key={k}><a href={l.url}>{l.label}</a></li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </nav>
        <div className="mm-icons">
          <a href="/giris" aria-label="Account"><User size={18}/></a>
          <button aria-label="Search"><Search size={18}/></button>
          <a href="/hesabim" aria-label="Wishlist"><Heart size={18}/></a>
          <a href="/sepet" aria-label="Bag"><ShoppingBag size={18}/></a>
        </div>
      </div>
    </header>
  );
}

function MobileMenu({ theme, menu, onClose }) {
  const [expanded, setExpanded] = useState(null);
  return (
    <div className="mm-mobile-menu" data-testid="mobile-menu">
      {menu.map((m, i) => (
        <div key={i} className="mm-mobile-item">
          <div className="mm-mobile-link" onClick={() => setExpanded(expanded === i ? null : i)}>
            <a href={m.url} className={m.accent ? 'mm-accent' : ''} onClick={onClose}>{m.label}</a>
            {(m.columns || []).length > 0 && <ChevronDown size={16} className={expanded === i ? 'rot' : ''}/>}
          </div>
          {expanded === i && (m.columns || []).length > 0 && (
            <div className="mm-mobile-sub">
              {(m.columns || []).map((col, j) => (
                <div key={j}>
                  {col.title && <h5>{col.title}</h5>}
                  {(col.links || []).map((l, k) => (
                    <a key={k} href={l.url} onClick={onClose}>{l.label}</a>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
      <div className="mm-mobile-foot">
        <a href="/giris" onClick={onClose}>Account</a>
        <a href="/hesabim" onClick={onClose}>Wishlist</a>
        <a href="/sepet" onClick={onClose}>Bag</a>
      </div>
    </div>
  );
}

/* ---------- Block Renderer ---------- */
function BlockRenderer({ block, idx, total, themeSlug }) {
  switch (block.type) {
    case "hero_fullscreen":
    case "editorial_card":
      return <FullScreenBlock block={block} idx={idx} total={total} />;
    case "product_scroller":
      return <ProductScroller block={block} themeSlug={themeSlug} />;
    case "newsletter":
      return <Newsletter block={block} />;
    case "text_section":
      return (
        <section className="mm-text">
          <h2>{block.title}</h2>
          {block.subtitle && <p>{block.subtitle}</p>}
        </section>
      );
    default:
      return null;
  }
}

function FullScreenBlock({ block, idx, total }) {
  const overlay = block.settings?.overlay ?? 0.3;
  const align = block.settings?.align || "center";
  const color = block.settings?.text_color || "#fff";
  return (
    <section className="mm-fs" data-testid={`block-${block.type}-${block.id}`}>
      <picture>
        {block.mobile_image && <source media="(max-width: 768px)" srcSet={block.mobile_image} />}
        <img src={block.image} alt={block.title || ""} loading={idx > 0 ? "lazy" : "eager"} />
      </picture>
      <div className="mm-fs-overlay" style={{ background: `rgba(0,0,0,${overlay})` }} />
      <div className={`mm-fs-content mm-fs-${align}`} style={{ color }}>
        <h2 className="mm-fs-title">{block.title}</h2>
        {block.subtitle && <p className="mm-fs-sub">{block.subtitle}</p>}
        {block.link_url && block.link_label && (
          <a href={block.link_url} className="mm-fs-cta" style={{ color, borderColor: color }}>{block.link_label}</a>
        )}
      </div>
      {total > 1 && (
        <div className="mm-counter" style={{ color }}>{idx + 1}<span>/{total}</span></div>
      )}
      {idx === 0 && (
        <div className="mm-scroll-hint" style={{ color }}>Scroll to explore<span className="mm-scroll-line"/></div>
      )}
    </section>
  );
}

function ProductScroller({ block, themeSlug }) {
  const [items, setItems] = useState([]);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const slug = block.settings?.category_slug;
        const limit = block.settings?.limit || 12;
        // 1) Try category-filtered list
        let raw = [];
        if (slug) {
          const r1 = await axios.get(`${API}/products`, { params: { category: slug, limit } });
          raw = Array.isArray(r1.data) ? r1.data : (r1.data.products || r1.data.items || []);
        }
        // 2) If empty (or no category specified), fall back to ALL active products
        if (raw.length === 0) {
          const r2 = await axios.get(`${API}/products`, { params: { limit } });
          raw = Array.isArray(r2.data) ? r2.data : (r2.data.products || r2.data.items || []);
        }
        if (!cancel) {
          setItems(raw.slice(0, limit));
          setLoaded(true);
        }
      } catch {
        if (!cancel) setLoaded(true);
      }
    })();
    return () => { cancel = true; };
  }, [block.settings?.category_slug, block.settings?.limit]);

  const display = items;

  return (
    <section className="mm-scroller" data-testid={`block-product-scroller-${block.id}`}>
      <div className="mm-scroller-head">
        <h2>{block.title}</h2>
        {block.link_url && block.link_label && <a href={block.link_url} className="mm-scroller-cta">{block.link_label}</a>}
      </div>
      <div className="mm-scroller-track">
        {display.map((p, i) => <ProductCard key={p.id || p.slug || i} p={p} themeSlug={themeSlug} />)}
      </div>
      {loaded && !display.length && <p className="mm-empty">Gösterilebilir ürün bulunamadı.</p>}
    </section>
  );
}

function ProductCard({ p, themeSlug }) {
  const imgs = (p.images && p.images.length) ? p.images : (p.image ? [p.image] : (p.thumbnail ? [p.thumbnail] : [PLACEHOLDER_IMG]));
  const primary = imgs[0];
  const hover = imgs[1] || imgs[0];
  const price = p.sale_price || p.price;
  const oldPrice = p.sale_price && p.price && p.price > p.sale_price ? p.price : null;
  const href = p.slug ? `/tema/${themeSlug}/urun/${p.slug}` : "#";
  return (
    <a href={href} className="mm-product-card" data-testid={`mm-product-${p.id || p.slug || 'placeholder'}`}>
      <div className="mm-product-img">
        <img src={primary} alt={p.name || p.title || ""} loading="lazy" className="mm-img-primary" />
        {hover && hover !== primary && (
          <img src={hover} alt="" loading="lazy" className="mm-img-hover" aria-hidden="true" />
        )}
        <button
          className="mm-product-wish"
          aria-label="Add to wishlist"
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); }}
        >
          <Heart size={14} />
        </button>
      </div>
      <div className="mm-product-name">{p.name || p.title || "Untitled"}</div>
      {price != null && (
        <div className="mm-product-price">
          {oldPrice && <span className="mm-price-old">{Number(oldPrice).toLocaleString("tr-TR")} ₺</span>}
          <span>{Number(price).toLocaleString("tr-TR")} ₺</span>
        </div>
      )}
    </a>
  );
}

function Newsletter({ block }) {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState(false);
  const submit = (e) => { e.preventDefault(); if (email) setDone(true); };
  return (
    <section className="mm-newsletter" style={{ background: block.settings?.bg || "#000", color: block.settings?.color || "#fff" }} data-testid="block-newsletter">
      <h2>{block.title}</h2>
      {block.subtitle && <p>{block.subtitle}</p>}
      {done ? (
        <div className="mm-newsletter-done">Thanks for subscribing.</div>
      ) : (
        <form onSubmit={submit} className="mm-newsletter-form">
          <input type="email" required placeholder="Email address" value={email} onChange={e => setEmail(e.target.value)} aria-label="Email" data-testid="newsletter-email"/>
          <button type="submit" data-testid="newsletter-submit">{block.link_label || "Subscribe"}</button>
        </form>
      )}
    </section>
  );
}

/* ---------- Footer ---------- */
function Footer({ theme, company }) {
  return (
    <footer className="mm-footer" data-testid="storefront-footer">
      <div className="mm-footer-grid">
        <div>
          <h5>Customer Care</h5>
          <ul>
            <li><a href="/iletisim">Contact us</a></li>
            <li><a href="/sayfa/sss">FAQs</a></li>
            <li><a href="/siparis-takip">Track order</a></li>
            <li><a href="/sayfa/iade">Returns &amp; refunds</a></li>
          </ul>
        </div>
        <div>
          <h5>Company</h5>
          <ul>
            <li><a href="/sayfa/hakkimizda">About us</a></li>
            <li><a href="/sayfa/magazalar">Stores</a></li>
            <li><a href="/sayfa/kariyer">Careers</a></li>
            <li><a href="/sayfa/surdurulebilirlik">Sustainability</a></li>
          </ul>
        </div>
        <div>
          <h5>Legal</h5>
          <ul>
            <li><a href="/sayfa/kvkk">Privacy policy</a></li>
            <li><a href="/sayfa/cerez">Cookie policy</a></li>
            <li><a href="/sayfa/satis-sartlari">Terms of sale</a></li>
          </ul>
        </div>
        <div>
          <h5>{company?.site_name || "Meluni"} sosyal</h5>
          <ul>
            <li><a href="#">Instagram</a></li>
            <li><a href="#">YouTube</a></li>
            <li><a href="#">TikTok</a></li>
            <li><a href="#">Pinterest</a></li>
          </ul>
        </div>
      </div>
      <div className="mm-footer-bot">
        <span>© {new Date().getFullYear()} {company?.site_name || "Meluni"}. Tüm hakları saklıdır.</span>
        <span>Türkiye · TR</span>
      </div>
    </footer>
  );
}

function StoreCategory({ slug, categories, products, themeSlug, title, compact = false }) {
  const category = categories.find(c => c.slug === slug);
  const filtered = !slug || slug === "tumu" ? products : products.filter(p => p.category_id === category?.id || p.category_slug === slug || (p.categories || []).includes(category?.id));
  return <section className={`mm-category ${compact ? "mm-category-compact" : ""}`} data-testid="theme-category-view">
    <header><small>{compact ? "MELUNI SEÇKİSİ" : "KOLEKSİYON"}</small><h1>{title || category?.name || "Tüm ürünler"}</h1>{!compact && <p>{filtered.length} ürün</p>}</header>
    <div className="mm-category-grid">{filtered.map((p,i)=><ProductCard key={p.id || p.slug || i} p={p} themeSlug={themeSlug}/>)}</div>
    {!filtered.length && <p className="mm-empty">Bu kategoride gösterilebilir ürün bulunamadı.</p>}
  </section>;
}

function StoreProduct({ product }) {
  if (!product) return <div className="mm-empty">Ürün bulunamadı.</div>;
  const images = Array.isArray(product.images) ? product.images : [];
  const gallery = images.length ? images : [themeImage(product)].filter(Boolean);
  return <section className="mm-product-detail" data-testid="theme-product-view"><div className="mm-product-gallery">{gallery.slice(0,4).map((img,i)=><img key={i} src={typeof img === "string" ? img : img?.url} alt={i ? "" : product.name || product.title}/>)}</div><aside><small>MELUNI</small><h1>{product.name || product.title}</h1><strong>{themePrice(product)}</strong><p>{product.short_description || "Renk ve beden seçeneklerini inceleyin."}</p><a href={product.slug ? `/urun/${product.slug}` : "#"}>Ürün sayfasında aç</a></aside></section>;
}

const PLACEHOLDER_IMG = "";
