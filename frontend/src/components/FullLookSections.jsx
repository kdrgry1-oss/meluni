import { priceView, fmtTL } from '../lib/price';
import './fullLook.css';

// renderProduct: site tarafında standart ürün kartı (ProductCard) — indirim rozeti, sepet ikonu,
// bedenler vb. site geneliyle aynı. Verilmezse (editör önizlemesi / test) sade kart çizilir.
export default function FullLookSections({ looks = [], interactive = true, startIndex = 0, renderProduct = null, renderAfterProducts = null, renderProducts = null }) {
  return <div className="full-look-flow" data-testid="full-look-flow">
    {looks.map((look, index) => <section className="full-look-section" id={`look-${look.id}`} key={look.id} aria-label={look.title || `Kombin ${startIndex + index + 1}`}>
      <div className="full-look-hero">
        {look.image ? <img src={look.image} alt={look.title || `Kombin ${startIndex + index + 1}`} loading={startIndex + index ? 'lazy' : 'eager'} />
          : <div className="full-look-placeholder">Soldaki kombin fotoğrafını seçin</div>}
      </div>
      <div className="full-look-selection" id={`look-products-${look.id}`}>
        {/* Kullanıcı isteği: görsel üstündeki "01" rozeti ve "Parçaları keşfet" kaldırıldı; "GÖRÜNÜMÜ TAMAMLA"
            yerine aynı font/boyutta her kombinin ürünlerinin üstünde LOOK 01, LOOK 02 … */}
        <div className="full-look-caption"><div className="full-look-caption-meta"><span className="full-look-label">LOOK {String(startIndex + index + 1).padStart(2, '0')}</span><span>{look.products?.length || 0} PARÇA</span></div>{look.title && <h2>{look.title}</h2>}</div>
        {/* renderProducts: site tarafında satır düzeni (görsel · ad · renk · fiyat · beden) + kombin özeti tek bileşen */}
        {renderProducts && !!look.products?.length ? renderProducts(look) : (
        <div className={`full-look-products${renderProduct ? ' full-look-products--cards' : ''}`}>
          {(look.products || []).map((product, pi) => {
            if (renderProduct) return <div className="full-look-product-card" key={product.id}>{renderProduct(product, pi)}</div>;
            const price = priceView(product);
            const content = <><div className="full-look-product-image">
              {product.images?.[0] ? <img src={product.images[0]} alt={product.name} loading="lazy" /> : <span>Görsel yok</span>}
            </div><h3>{product.name}</h3><p className="full-look-price">
              {price.hasDiscount && <del>{fmtTL(price.list)}</del>}<span>{fmtTL(price.display)}</span>
            </p><span className="full-look-product-action">Ürünü incele <span aria-hidden="true">↗</span></span></>;
            return interactive ? <a className="full-look-product" key={product.id} href={`/urun/${encodeURIComponent(product.slug || product.id)}`}>{content}</a>
              : <div className="full-look-product" key={product.id}>{content}</div>;
          })}
        </div>)}
        {!look.products?.length && <p className="full-look-empty-products">Bu görseldeki ürünleri sağ tarafa ekleyin.</p>}
        {!renderProducts && renderAfterProducts && !!look.products?.length && renderAfterProducts(look)}
      </div>
    </section>)}
  </div>;
}
