"""
Trendyol API Client for advanced integrations.
Includes Product Category, Attribute, and Brand fetching.
"""
import base64
import logging
from typing import List, Dict, Optional, Any
import httpx

logger = logging.getLogger(__name__)

class TrendyolClient:
    def __init__(self, supplier_id: str, api_key: str, api_secret: str, mode: str = "live"):
        self.supplier_id = supplier_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.mode = mode
        
        # Determine base URL based on mode
        if self.mode == "live":
            self.base_url = "https://apigw.trendyol.com/integration"
        else:
            self.base_url = "https://stageapigw.trendyol.com/integration"
            
        # Ensure credentials are provided
        if not self.supplier_id or not self.api_key or not self.api_secret:
            logger.warning("TrendyolClient initialized with missing credentials.")

    def _get_headers(self) -> Dict[str, str]:
        """Provides the Basic Auth headers necessary for Trendyol API."""
        credentials = f"{self.api_key}:{self.api_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        return {
            "Authorization": f"Basic {encoded_credentials}",
            "User-Agent": f"{self.supplier_id} - FacetteIntegration",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    async def _async_get(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        url = f"{self.base_url}{endpoint}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                # Trendyol Category endpoints do not strictly require Auth, but we send it anyway
                # Some endpoints (like brands) might fail if Auth is not sent.
                headers = self._get_headers() if self.api_key else {}
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"Trendyol API HTTP error on {endpoint}: {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Trendyol API connection error on {endpoint}: {str(e)}")
                raise

    # ------------------ CATEGORIES ------------------

    async def get_categories(self) -> List[Dict]:
        """
        Fetches the entire category tree from Trendyol.
        Endpoint: GET /product/product-categories
        """
        data = await self._async_get("/product/product-categories")
        return data.get("categories", [])

    async def get_category_attributes(self, category_id: int) -> List[Dict]:
        """
        Fetches the required and optional attributes for a specific category.
        This includes size, color, waist, length, season, etc.
        Endpoint: GET /product/product-categories/{categoryId}/attributes
        """
        data = await self._async_get(f"/product/product-categories/{category_id}/attributes")
        return data.get("categoryAttributes", [])

    # ------------------ BRANDS ------------------

    async def get_brands(self, size: int = 500, page: int = 0) -> Dict:
        """
        Fetches brands from Trendyol, paginated.
        Endpoint: GET /brands/by-name?size={size}&page={page}
        Wait, standard endpoint is /brands. Let's use /brands
        """
        data = await self._async_get("/brands", params={"size": size, "page": page})
        return data
        
    # ------------------ ATTRIBUTES METADATA ------------------
    # Additional helpers to be added for endpoints like Providers, ShipmentProviders if needed.

    # ------------------ PRODUCTS ------------------

    async def create_products(self, items: List[Dict]) -> Dict:
        """
        Sends a batch of products to Trendyol.
        Endpoint (v2 — Aug 2026+): POST /integration/product/sellers/{sellerId}/v2/products
        """
        url = f"{self.base_url}/product/sellers/{self.supplier_id}/v2/products"
        async with httpx.AsyncClient(timeout=60.0) as client:
            headers = self._get_headers()
            try:
                response = await client.post(url, headers=headers, json={"items": items})
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"Trendyol Product Create Error: {e.response.text}")
                try:
                    return e.response.json()
                except:
                    raise
            except Exception as e:
                logger.error(f"Trendyol Product API Error: {str(e)}")
                raise

    async def update_products(self, items: List[Dict]) -> Dict:
        """
        Updates existing products on Trendyol (matched by stockCode or barcode).
        Endpoint: PUT /integration/product/sellers/{sellerId}/products
        Use this when create_products fails with "Aynı barkodlu ürün var" — Trendyol
        rejects creates when stockCode+seller is already registered with a different barcode.
        Update changes existing record's barcode/attributes/prices.
        """
        url = f"{self.base_url}/product/sellers/{self.supplier_id}/products"
        async with httpx.AsyncClient(timeout=60.0) as client:
            headers = self._get_headers()
            try:
                response = await client.put(url, headers=headers, json={"items": items})
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"Trendyol Product Update Error: {e.response.text}")
                try:
                    return e.response.json()
                except Exception:
                    raise
            except Exception as e:
                logger.error(f"Trendyol Update API Error: {str(e)}")
                raise



    async def get_filtered_products(
        self,
        barcode: Optional[str] = None,
        stock_code: Optional[str] = None,
        product_main_id: Optional[str] = None,
        approved: Optional[bool] = None,
        archived: Optional[bool] = None,
        on_sale: Optional[bool] = None,
        page: int = 0,
        size: int = 50,
    ) -> Dict:
        """
        Trendyol'da seller'ın ürünlerini filtrelerle listeler (üst limit size=200).
        Endpoint: GET /integration/product/sellers/{sellerId}/products
        Response: {"content":[{... barcode, stockCode, approved, archived, onSale ...}], "totalElements":N, "totalPages":..., "page":...}
        """
        params: Dict[str, Any] = {"page": page, "size": min(max(size, 1), 200)}
        if barcode is not None: params["barcode"] = barcode
        if stock_code is not None: params["stockCode"] = stock_code
        if product_main_id is not None: params["productMainId"] = product_main_id
        if approved is not None: params["approved"] = str(approved).lower()
        if archived is not None: params["archived"] = str(archived).lower()
        if on_sale is not None: params["onSale"] = str(on_sale).lower()
        url = f"{self.base_url}/product/sellers/{self.supplier_id}/products"
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(url, headers=self._get_headers(), params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"Trendyol get_filtered_products error: {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Trendyol get_filtered_products connection error: {str(e)}")
                raise

    async def archive_products(self, barcodes: List[str]) -> Dict:
        """
        Ürünleri arşivler. Trendyol v3:
          PUT /integration/product/sellers/{sellerId}/products/archive-state
          Body: {"items":[{"barcode":"...","archived":true}, ...]}
        """
        url = f"{self.base_url}/product/sellers/{self.supplier_id}/products/archive-state"
        items = [{"barcode": b, "archived": True} for b in barcodes if b]
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                response = await client.put(url, headers=self._get_headers(), json={"items": items})
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"Trendyol archive_products error: {e.response.text}")
                try:
                    return e.response.json()
                except Exception:
                    raise
            except Exception as e:
                logger.error(f"Trendyol archive_products connection error: {str(e)}")
                raise

    async def unarchive_products(self, barcodes: List[str]) -> Dict:
        """Arşivden çıkar: PUT archive-state with archived=False."""
        url = f"{self.base_url}/product/sellers/{self.supplier_id}/products/archive-state"
        items = [{"barcode": b, "archived": False} for b in barcodes if b]
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                response = await client.put(url, headers=self._get_headers(), json={"items": items})
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"Trendyol unarchive_products error: {e.response.text}")
                try:
                    return e.response.json()
                except Exception:
                    raise
            except Exception as e:
                logger.error(f"Trendyol unarchive_products connection error: {str(e)}")
                raise

    async def get_batch_request_result(self, batch_request_id: str) -> Dict:
        """
        Checks the status of a batch request (e.g. product creation, price/inventory updates).
        Endpoint (v2): GET /integration/product/sellers/{sellerId}/products/batch-requests/{batchRequestId}
        """
        url = f"{self.base_url}/product/sellers/{self.supplier_id}/products/batch-requests/{batch_request_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = self._get_headers()
            try:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                logger.error(f"Trendyol Batch Request Error for {batch_request_id}: {str(e)}")
                raise

    async def update_price_and_inventory(self, items: List[Dict]) -> Dict:
        """
        Updates price and stock for products by barcode.
        items format: [{"barcode": "123", "quantity": 10, "salePrice": 100, "listPrice": 120}, ...]
        Endpoint (v2): POST /integration/inventory/sellers/{sellerId}/products/price-and-inventory
        """
        url = f"{self.base_url}/inventory/sellers/{self.supplier_id}/products/price-and-inventory"
        async with httpx.AsyncClient(timeout=60.0) as client:
            headers = self._get_headers()
            try:
                response = await client.post(url, headers=headers, json={"items": items})
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"Trendyol Price/Inventory Update Error: {e.response.text}")
                try:
                    return e.response.json()
                except:
                    raise
            except Exception as e:
                logger.error(f"Trendyol Price/Inventory API Error: {str(e)}")
                raise

    # ------------------ ORDERS ------------------

    async def get_orders(self, start_date_ms: int = None, end_date_ms: int = None, status: str = None, order_number: str = None, size: int = 50, page: int = 0) -> Dict:
        """
        Fetches orders from Trendyol.
        Endpoint: GET /order/sellers/{supplierId}/orders
        """
        url = f"{self.base_url}/order/sellers/{self.supplier_id}/orders"
        params = {"size": size, "page": page}
        if start_date_ms:
            params["startDate"] = start_date_ms
        if end_date_ms:
            params["endDate"] = end_date_ms
        if status:
            params["status"] = status
        if order_number:
            params["orderNumber"] = order_number
        else:
            # Sadece order_number yoksa siralamayi ekle, spesifik sipariste siralama istenmez
            params["orderByField"] = "PackageLastModifiedDate"
            params["orderByDirection"] = "DESC"
            
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = self._get_headers()
            try:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"Trendyol Get Orders Error: {e.response.text}")
                raise Exception(f"Trendyol API Error: {e.response.text}")
            except Exception as e:
                logger.error(f"Trendyol Get Orders API Error: {str(e)}")
                raise

    # ------------------ CARGO / SHIPMENT ------------------

    async def get_cargo_label(self, cargo_tracking_number: str) -> bytes:
        """
        Fetches the cargo label in PDF format as bytes.
        Endpoint: GET /suppliers/{supplierId}/common-label/{cargoTrackingNumber}?format=pdf
        """
        url = f"{self.base_url}/suppliers/{self.supplier_id}/common-label/{cargo_tracking_number}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = self._get_headers()
            try:
                response = await client.get(url, headers=headers, params={"format": "pdf"})
                response.raise_for_status()
                return response.content
            except Exception as e:
                logger.error(f"Trendyol Cargo Label Error for {cargo_tracking_number}: {str(e)}")
                raise
