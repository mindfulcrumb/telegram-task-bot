"""Custom product database — user-contributed products from nutrition labels.

When a barcode isn't found in Open Food Facts, users can take a photo
of the nutrition label. Zoe extracts the data and stores it here so
future scans of the same barcode return instant results.
"""
import logging

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)


def get_product_by_barcode(barcode: str) -> dict | None:
    """Look up a custom product by barcode."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM custom_products WHERE barcode = %s",
            (barcode,)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def save_product(barcode: str, product_name: str, brand: str = None,
                 serving_size_g: float = None,
                 calories_per_100g: float = None,
                 protein_per_100g: float = None,
                 carbs_per_100g: float = None,
                 fat_per_100g: float = None,
                 fiber_per_100g: float = None,
                 sodium_per_100g: float = None,
                 calcium_per_100g: float = None,
                 iron_per_100g: float = None,
                 potassium_per_100g: float = None,
                 vitamin_c_per_100g: float = None,
                 vitamin_d_per_100g: float = None,
                 b12_per_100g: float = None,
                 magnesium_per_100g: float = None,
                 zinc_per_100g: float = None,
                 created_by: int = None) -> dict:
    """Save or update a custom product. Upserts on barcode."""
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO custom_products
               (barcode, product_name, brand, serving_size_g,
                calories_per_100g, protein_per_100g, carbs_per_100g,
                fat_per_100g, fiber_per_100g, sodium_per_100g,
                calcium_per_100g, iron_per_100g, potassium_per_100g,
                vitamin_c_per_100g, vitamin_d_per_100g, b12_per_100g,
                magnesium_per_100g, zinc_per_100g, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (barcode) DO UPDATE SET
                 product_name = EXCLUDED.product_name,
                 brand = EXCLUDED.brand,
                 serving_size_g = EXCLUDED.serving_size_g,
                 calories_per_100g = EXCLUDED.calories_per_100g,
                 protein_per_100g = EXCLUDED.protein_per_100g,
                 carbs_per_100g = EXCLUDED.carbs_per_100g,
                 fat_per_100g = EXCLUDED.fat_per_100g,
                 fiber_per_100g = EXCLUDED.fiber_per_100g,
                 sodium_per_100g = EXCLUDED.sodium_per_100g,
                 calcium_per_100g = EXCLUDED.calcium_per_100g,
                 iron_per_100g = EXCLUDED.iron_per_100g,
                 potassium_per_100g = EXCLUDED.potassium_per_100g,
                 vitamin_c_per_100g = EXCLUDED.vitamin_c_per_100g,
                 vitamin_d_per_100g = EXCLUDED.vitamin_d_per_100g,
                 b12_per_100g = EXCLUDED.b12_per_100g,
                 magnesium_per_100g = EXCLUDED.magnesium_per_100g,
                 zinc_per_100g = EXCLUDED.zinc_per_100g,
                 updated_at = NOW()
               RETURNING *""",
            (barcode, product_name, brand, serving_size_g,
             calories_per_100g, protein_per_100g, carbs_per_100g,
             fat_per_100g, fiber_per_100g, sodium_per_100g,
             calcium_per_100g, iron_per_100g, potassium_per_100g,
             vitamin_c_per_100g, vitamin_d_per_100g, b12_per_100g,
             magnesium_per_100g, zinc_per_100g, created_by)
        )
        result = dict(cur.fetchone())
        logger.info(f"Custom product saved: {product_name} (barcode={barcode})")
        return result


def to_nutrition_dict(product: dict) -> dict:
    """Convert a custom product row to the same format as openfoodfacts_service."""
    return {
        "name": product.get("product_name"),
        "brand": product.get("brand"),
        "barcode": product.get("barcode"),
        "quantity": f"{product['serving_size_g']}g" if product.get("serving_size_g") else None,
        "nutriscore": None,
        "nutrition_per_100g": {
            "calories": product.get("calories_per_100g"),
            "protein_g": product.get("protein_per_100g"),
            "carbs_g": product.get("carbs_per_100g"),
            "fat_g": product.get("fat_per_100g"),
            "fiber_g": product.get("fiber_per_100g"),
            "sodium_mg": product.get("sodium_per_100g"),
            "calcium_mg": product.get("calcium_per_100g"),
            "iron_mg": product.get("iron_per_100g"),
            "potassium_mg": product.get("potassium_per_100g"),
            "vitamin_c_mg": product.get("vitamin_c_per_100g"),
            "vitamin_d_mcg": product.get("vitamin_d_per_100g"),
            "b12_mcg": product.get("b12_per_100g"),
            "magnesium_mg": product.get("magnesium_per_100g"),
            "zinc_mg": product.get("zinc_per_100g"),
        },
        "source": "custom_product_db",
    }
