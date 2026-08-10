import { useState, useEffect } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { X } from "lucide-react";
import axios from "axios";
import Header from "../components/Header";
import Footer from "../components/Footer";
import ProductCard from "../components/ProductCard";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

export default function Search() {
  const [searchParams] = useSearchParams();
  const query = searchParams.get("q") || "";
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [searchInput, setSearchInput] = useState(query);

  useEffect(() => {
    if (query) {
      searchProducts();
    }
  }, [query]);

  const searchProducts = async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API}/products?search=${encodeURIComponent(query)}&limit=40`);
      setProducts(res.data?.products || []);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen" data-testid="search-page">
      <Header />

      <div className="container-main py-8">
        {/* Search Header */}
        <div className="text-center mb-12">
          <h1 className="text-3xl md:text-4xl font-light mb-4">
            {query ? `"${query}" için sonuçlar` : "Arama"}
          </h1>
          {products.length > 0 && (
            <p className="text-gray-500">{products.length} ürün bulundu</p>
          )}
        </div>

        {/* Results */}
        {loading ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 md:gap-6">
            {[...Array(8)].map((_, i) => (
              <div key={i} className="animate-pulse">
                <div className="aspect-[2/3] bg-gray-200" />
                <div className="mt-3 space-y-2">
                  <div className="h-3 bg-gray-200 w-1/3" />
                  <div className="h-4 bg-gray-200 w-2/3" />
                  <div className="h-4 bg-gray-200 w-1/4" />
                </div>
              </div>
            ))}
          </div>
        ) : products.length === 0 && query ? (
          <div className="text-center py-16">
            <p className="text-gray-500 mb-4">Aramanızla eşleşen ürün bulunamadı.</p>
            <Link to="/" className="btn-secondary">Ana Sayfaya Dön</Link>
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 md:gap-6">
            {products.map((product) => (
              <ProductCard key={product.id} product={product} />
            ))}
          </div>
        )}
      </div>

      <Footer />
    </div>
  );
}
