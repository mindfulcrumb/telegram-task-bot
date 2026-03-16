"""Open Food Facts product lookup by barcode and name search.

Free, no API key required. 4M+ products across 150 countries.
Covers EAN-13 (Europe), UPC-A (US), and other barcode formats.
Includes Portuguese/European product search via country-specific subdomains.
API docs: https://openfoodfacts.github.io/openfoodfacts-server/api/
"""
import logging
from functools import lru_cache

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://world.openfoodfacts.org/api/v2/product"
_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"
_PT_SEARCH_URL = "https://pt.openfoodfacts.org/cgi/search.pl"
_FIELDS = "product_name,brands,nutriments,nutriscore_grade,categories,quantity,image_url"
_USER_AGENT = "ZoeBot/1.0 (telegram-task-bot; https://meetzoe.app)"
_TIMEOUT = 10.0


@lru_cache(maxsize=200)
def lookup_barcode(barcode: str) -> dict | None:
    """Look up a product by barcode in Open Food Facts.

    Returns a normalized dict with product info and nutrition per 100g,
    or None if product not found.
    """
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(
                f"{_BASE_URL}/{barcode}",
                params={"fields": _FIELDS},
                headers={"User-Agent": _USER_AGENT},
            )

        if resp.status_code != 200:
            logger.warning(f"OFF API returned {resp.status_code} for barcode {barcode}")
            return None

        data = resp.json()

        if data.get("status") != 1:
            logger.info(f"Product not found in OFF: {barcode}")
            return None

        product = data.get("product", {})
        nutriments = product.get("nutriments", {})

        # Extract nutrition per 100g
        nutrition = _extract_nutrition(nutriments)

        result = {
            "name": product.get("product_name", "").strip() or None,
            "brand": product.get("brands", "").strip() or None,
            "barcode": barcode,
            "nutriscore": product.get("nutriscore_grade"),
            "categories": product.get("categories", ""),
            "quantity": product.get("quantity", ""),
            "image_url": product.get("image_url"),
            "nutrition_per_100g": nutrition,
            "source": "openfoodfacts",
        }

        logger.info(
            f"OFF product found: {result['name']} ({result['brand']}) "
            f"— {nutrition.get('calories', '?')} kcal/100g"
        )
        return result

    except httpx.TimeoutException:
        logger.warning(f"OFF API timeout for barcode {barcode}")
        return None
    except Exception as e:
        logger.error(f"OFF lookup failed for {barcode}: {type(e).__name__}: {e}")
        return None


def _extract_nutrition(nutriments: dict) -> dict:
    """Extract and normalize nutrition data from OFF nutriments object.

    OFF stores most values per 100g. Some micronutrients are in grams
    and need conversion to mg/mcg for Zoe's log_meal format.
    """
    def _get(key: str, multiplier: float = 1.0) -> float | None:
        val = nutriments.get(key)
        if val is None:
            return None
        try:
            return round(float(val) * multiplier, 2)
        except (ValueError, TypeError):
            return None

    return {
        "calories": _get("energy-kcal_100g"),
        "protein_g": _get("proteins_100g"),
        "carbs_g": _get("carbohydrates_100g"),
        "fat_g": _get("fat_100g"),
        "fiber_g": _get("fiber_100g"),
        # Micronutrients — OFF stores in g, convert to mg
        "sodium_mg": _get("sodium_100g", 1000),
        "calcium_mg": _get("calcium_100g", 1000),
        "iron_mg": _get("iron_100g", 1000),
        "potassium_mg": _get("potassium_100g", 1000),
        "vitamin_c_mg": _get("vitamin-c_100g", 1000),
        "magnesium_mg": _get("magnesium_100g", 1000),
        "zinc_mg": _get("zinc_100g", 1000),
        # Vitamin D: OFF stores in g, convert to mcg (× 1,000,000)
        "vitamin_d_mcg": _get("vitamin-d_100g", 1000000),
        # B12: OFF stores in g, convert to mcg (× 1,000,000)
        "b12_mcg": _get("vitamin-b12_100g", 1000000),
    }


def search_by_name(query: str, country: str = "portugal") -> dict | None:
    """Search Open Food Facts by product name. Tries country-specific first, then global.

    Useful when barcode lookup fails but we have the product name (e.g., from a label photo).
    Returns the same normalized dict as lookup_barcode().
    """
    # Try Portuguese OFF first for PT products, then global
    urls = [_PT_SEARCH_URL, _SEARCH_URL] if country == "portugal" else [_SEARCH_URL]

    for url in urls:
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.get(
                    url,
                    params={
                        "search_terms": query,
                        "search_simple": 1,
                        "action": "process",
                        "json": 1,
                        "page_size": 3,
                        "fields": _FIELDS + ",code",
                    },
                    headers={"User-Agent": _USER_AGENT},
                )

            if resp.status_code != 200:
                continue

            data = resp.json()
            products = data.get("products", [])

            if not products:
                continue

            # Pick best match (first result with nutrition data)
            for product in products:
                nutriments = product.get("nutriments", {})
                if not nutriments.get("energy-kcal_100g"):
                    continue

                nutrition = _extract_nutrition(nutriments)

                result = {
                    "name": product.get("product_name", "").strip() or None,
                    "brand": product.get("brands", "").strip() or None,
                    "barcode": product.get("code", ""),
                    "nutriscore": product.get("nutriscore_grade"),
                    "categories": product.get("categories", ""),
                    "quantity": product.get("quantity", ""),
                    "image_url": product.get("image_url"),
                    "nutrition_per_100g": nutrition,
                    "source": "openfoodfacts",
                }

                if result["name"]:
                    logger.info(
                        f"OFF search found: {result['name']} ({result['brand']}) "
                        f"— {nutrition.get('calories', '?')} kcal/100g"
                    )
                    return result

        except httpx.TimeoutException:
            logger.warning(f"OFF search timeout for '{query}' at {url}")
            continue
        except Exception as e:
            logger.error(f"OFF search failed for '{query}': {type(e).__name__}: {e}")
            continue

    return None
