# Routes package
from .auth import router as auth_router
from .products import router as products_router
from .orders import router as orders_router
from .categories import router as categories_router
from .banners import router as banners_router
from .cms import router as cms_router
from .integrations import router as integrations_router
from .admin import router as admin_router
from .customer import router as customer_router
from .variants import router as variants_router
from .webhooks import router as webhooks_router
from .attributes import router as attributes_router
from .upload import router as upload_router
from .upload import files_router as upload_files_router
from .settings import router as settings_router

__all__ = [
    "auth_router",
    "products_router", 
    "orders_router",
    "categories_router",
    "banners_router",
    "cms_router",
    "integrations_router",
    "admin_router",
    "customer_router",
    "variants_router",
    "webhooks_router",
    "attributes_router",
    "upload_router",
    "upload_files_router",
    "settings_router",
]
