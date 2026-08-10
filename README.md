# Facette / Ruby Nova - E-Ticaret Yönetim Paneli

Bu proje, Facette markası için geliştirilmiş, ürün yönetimi, sipariş takibi ve pazaryeri entegrasyonlarını (Trendyol, Ticimax vb.) kapsayan modern bir e-ticaret yönetim sistemidir.

## Teknoloji Yığını

- **Frontend:** React (Vite/CRA), TailwindCSS, Lucide Icons, Axios.
- **Backend:** FastAPI (Python 3.9+), MongoDB (Motor/PyMongo), Uvicorn.
- **Veritabanı:** MongoDB (JSON tabanlı esnek veri yapısı).

## Proje Yapısı ve Ürün Yönetimi

Eğer ürünleri veya ilgili kodları arıyorsanız, şu dizinlere göz atabilirsiniz:

### 1. Ürün Yönetimi (Products)
- **Backend Mantığı:** `backend/routes/products.py` — Ürün oluşturma, düzenleme, silme ve veritabanı işlemlerinin yapıldığı yerdir.
- **Frontend Arayüzü:** `frontend/src/pages/admin/Products.jsx` — Admin panelindeki ürün listesi, filtreleme ve düzenleme modalı buradadır.
- **Veri Yapısı:** MongoDB içerisindeki `products` koleksiyonunda saklanır.

### 2. Entegrasyonlar (Integrations)
- **Trendyol & Ticimax:** `backend/routes/integrations.py` — Tüm dış platform senkronizasyon mantığı (stok, fiyat, aktarım) buradadı.
- **Trendyol Arayüzü:** `frontend/src/pages/admin/TrendyolEslestir.jsx` — Ürünlerin Trendyol ile eşleştirildiği ana ekrandır.
- **Log Sistemi:** `frontend/src/pages/admin/TrendyolLogs.jsx` — Tüm aktarım işlemlerinin kayıtlarının tutulduğu ekrandır.

### 3. Diğer Önemli Dizinler
- `backend/server.py`: Uygulamanın giriş noktası ve tüm router tanımlamaları.
- `backend/routes/`: Kategoriler, Siparişler, Bannerlar ve Ayarlar için API uç noktaları.
- `frontend/src/pages/admin/`: Admin panelindeki tüm sayfa bileşenleri.

## Kurulum ve Çalıştırma

### Backend
```bash
cd backend
pip install -r requirements.txt
python3 -m uvicorn server:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run start
```

## Notlar
Veritabanı verileri (ürünler, siparişler vb.) GitHub'da **tutulmaz**. Bu bilgiler yerel veya bulut MongoDB sunucunuzda saklanır. Kodları başka bir yere taşıdığınızda MongoDB yedeğinizi (dump) de taşımanız gerekir.
