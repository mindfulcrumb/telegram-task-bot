"""USDA FoodData Central API client — async, with in-memory cache."""
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

USDA_BASE = "https://api.nal.usda.gov/fdc/v1"

# Nutrient ID mapping (USDA FDC nutrient IDs)
NUTRIENT_IDS = {
    "calories": [1008, 2047, 2048],  # 1008=Energy, 2047=Atwater General, 2048=Atwater Specific
    "protein": [1003],
    "fat": [1004],
    "carbs": [1005],
    "fiber": [1079],
    "vitamin_d": [1114],
    "magnesium": [1090],
    "zinc": [1095],
    "iron": [1089],
    "b12": [1178],
    "potassium": [1092],
    "vitamin_c": [1162],
    "calcium": [1087],
    "sodium": [1093],
}

# Flat list of all nutrient IDs for API filter
_ALL_IDS = sorted({nid for ids in NUTRIENT_IDS.values() for nid in ids})

# In-memory cache: query_normalized -> {fdcId, nutrients_per_100g, ...}
_nutrient_cache: dict[str, dict] = {}
_CACHE_MAX = 500


def _get_api_key() -> str:
    return os.environ.get("USDA_API_KEY", "")


async def lookup_barcode(barcode: str) -> Optional[dict]:
    """Look up a product by barcode (GTIN/UPC) in USDA FoodData Central.

    Returns a normalized dict matching openfoodfacts format, or None.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.warning("USDA_API_KEY not set — skipping USDA barcode lookup")
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{USDA_BASE}/foods/search",
                params={
                    "api_key": api_key,
                    "query": barcode,
                    "pageSize": 3,
                },
            )
            if resp.status_code != 200:
                logger.warning(f"USDA barcode search returned {resp.status_code} for {barcode}")
                return None

            foods = resp.json().get("foods", [])
            if not foods:
                logger.info(f"Barcode {barcode} not found in USDA")
                return None

            # Pick the best match — prefer Branded items for barcode matches
            best = foods[0]
            for f in foods:
                if f.get("gtinUpc") == barcode:
                    best = f
                    break

            # Extract inline nutrients from search result
            food_nutrients = {n.get("nutrientId"): n.get("value", 0)
                             for n in best.get("foodNutrients", [])}

            nutrition = {}
            for name, ids in NUTRIENT_IDS.items():
                for nid in ids:
                    val = food_nutrients.get(nid)
                    if val is not None and val > 0:
                        nutrition[name] = round(val, 2)
                        break

            # Normalize to match openfoodfacts_service format
            result = {
                "name": best.get("description", "").strip() or None,
                "brand": best.get("brandOwner", "").strip() or best.get("brandName", "").strip() or None,
                "barcode": barcode,
                "nutriscore": None,
                "categories": best.get("foodCategory", ""),
                "quantity": best.get("servingSize", ""),
                "image_url": None,
                "nutrition_per_100g": {
                    "calories": nutrition.get("calories"),
                    "protein_g": nutrition.get("protein"),
                    "carbs_g": nutrition.get("carbs"),
                    "fat_g": nutrition.get("fat"),
                    "fiber_g": nutrition.get("fiber"),
                    "sodium_mg": round(nutrition["sodium"], 2) if nutrition.get("sodium") else None,
                    "calcium_mg": round(nutrition["calcium"], 2) if nutrition.get("calcium") else None,
                    "iron_mg": round(nutrition["iron"], 2) if nutrition.get("iron") else None,
                    "potassium_mg": round(nutrition["potassium"], 2) if nutrition.get("potassium") else None,
                    "vitamin_c_mg": round(nutrition["vitamin_c"], 2) if nutrition.get("vitamin_c") else None,
                    "magnesium_mg": round(nutrition["magnesium"], 2) if nutrition.get("magnesium") else None,
                    "zinc_mg": round(nutrition["zinc"], 2) if nutrition.get("zinc") else None,
                    "vitamin_d_mcg": round(nutrition["vitamin_d"], 2) if nutrition.get("vitamin_d") else None,
                    "b12_mcg": round(nutrition["b12"], 2) if nutrition.get("b12") else None,
                },
                "source": "usda",
            }

            logger.info(
                f"USDA product found for barcode {barcode}: {result['name']} ({result['brand']}) "
                f"— {nutrition.get('calories', '?')} kcal/100g"
            )
            return result

    except Exception as e:
        logger.warning(f"USDA barcode lookup failed for {barcode}: {type(e).__name__}: {e}")
        return None


async def search_food(query: str) -> Optional[dict]:
    """Search USDA for a food item. Waterfall: Foundation/SR Legacy -> FNDDS -> all.

    Returns {"fdcId": int, "description": str, "dataType": str} or None.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.warning("USDA_API_KEY not set — skipping USDA lookup")
        return None

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Try Foundation + SR Legacy first (lab-measured, most accurate)
        for data_types in [
            ["Foundation", "SR Legacy"],
            ["Survey (FNDDS)"],
            None,  # all types
        ]:
            payload = {"query": query, "pageSize": 5}
            if data_types:
                payload["dataType"] = data_types

            try:
                resp = await client.post(
                    f"{USDA_BASE}/foods/search",
                    params={"api_key": api_key},
                    json=payload,
                )
                if resp.status_code != 200:
                    logger.warning(f"USDA search returned {resp.status_code} for '{query}'")
                    continue

                foods = resp.json().get("foods", [])
                if foods:
                    best = foods[0]
                    return {
                        "fdcId": best["fdcId"],
                        "description": best.get("description", ""),
                        "dataType": best.get("dataType", ""),
                    }
            except Exception as e:
                logger.warning(f"USDA search failed for '{query}': {type(e).__name__}: {e}")
                return None

    return None


async def get_nutrients(fdc_id: int) -> Optional[dict]:
    """Get nutrient data for a food by FDC ID. Returns per-100g values.

    Keys: calories, protein, fat, carbs, fiber, vitamin_d, magnesium,
    zinc, iron, b12, potassium, vitamin_c, calcium, sodium
    """
    api_key = _get_api_key()
    if not api_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{USDA_BASE}/food/{fdc_id}",
                params={
                    "api_key": api_key,
                    "nutrients": ",".join(str(n) for n in _ALL_IDS),
                },
            )
            if resp.status_code != 200:
                logger.warning(f"USDA nutrient fetch returned {resp.status_code} for fdcId={fdc_id}")
                return None

            data = resp.json()
    except Exception as e:
        logger.warning(f"USDA nutrient fetch failed for fdcId={fdc_id}: {type(e).__name__}: {e}")
        return None

    nutrients: dict[str, float] = {}
    for n in data.get("foodNutrients", []):
        nid = None
        # Detail endpoint format varies by dataType
        nutrient_obj = n.get("nutrient", {})
        if nutrient_obj:
            nid = nutrient_obj.get("id")
        if nid is None:
            nid = n.get("number")
        if nid is None:
            continue
        nid = int(nid)

        amount = n.get("amount", 0) or 0

        for name, ids in NUTRIENT_IDS.items():
            if nid in ids:
                if name == "calories":
                    # Foundation may report kJ via 2047 — convert
                    if nid == 2047:
                        amount = amount / 4.184
                    # Keep first non-zero value
                    if name in nutrients and nutrients[name] > 0:
                        continue
                nutrients[name] = round(amount, 2)

    return nutrients if nutrients else None


async def search_and_get_nutrients(query: str) -> Optional[dict]:
    """Search + get nutrients in one call. Returns per-100g dict with metadata."""
    cache_key = query.lower().strip()
    if cache_key in _nutrient_cache:
        return _nutrient_cache[cache_key]

    food = await search_food(query)
    if not food:
        return None

    nutrients = await get_nutrients(food["fdcId"])
    if not nutrients:
        return None

    nutrients["usda_description"] = food["description"]
    nutrients["fdc_id"] = food["fdcId"]
    nutrients["data_type"] = food.get("dataType", "")

    # Cache it
    if len(_nutrient_cache) >= _CACHE_MAX:
        # Evict oldest half
        keys = list(_nutrient_cache.keys())
        for k in keys[: _CACHE_MAX // 2]:
            del _nutrient_cache[k]
    _nutrient_cache[cache_key] = nutrients

    return nutrients


def scale_nutrients(nutrients_per_100g: dict, portion_grams: float) -> dict:
    """Scale per-100g nutrients to actual portion size."""
    factor = portion_grams / 100.0
    scaled = {}
    for key, value in nutrients_per_100g.items():
        if isinstance(value, (int, float)):
            scaled[key] = round(value * factor, 2)
        else:
            scaled[key] = value
    return scaled


def sum_nutrients(items: list[dict]) -> dict:
    """Sum nutrient dicts from multiple food items into a meal total."""
    total: dict[str, float] = {}
    for item in items:
        for key, value in item.items():
            if isinstance(value, (int, float)):
                total[key] = round(total.get(key, 0) + value, 2)
    return total
