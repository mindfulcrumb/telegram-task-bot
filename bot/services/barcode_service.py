"""Barcode detection from images using pyzbar.

Decodes EAN-13, EAN-8, UPC-A, UPC-E, and QR codes from product photos.
For QR codes containing GS1 Digital Link URLs, extracts the embedded GTIN.
"""
import logging
import re

logger = logging.getLogger(__name__)


def detect_barcode(image_path: str) -> str | None:
    """Detect and decode a barcode from an image file.

    Returns the barcode string (e.g., "3017620422003") or None if no barcode found.
    """
    try:
        from pyzbar.pyzbar import decode
        from PIL import Image

        img = Image.open(image_path)
        results = decode(img)

        if not results:
            return None

        for result in results:
            data = result.data.decode("utf-8", errors="ignore").strip()
            barcode_type = result.type

            # Standard product barcodes — return directly
            if barcode_type in ("EAN13", "EAN8", "UPCA", "UPCE"):
                logger.info(f"Barcode detected: {barcode_type} = {data}")
                return data

            # QR codes may contain GS1 Digital Link URLs with embedded GTINs
            if barcode_type == "QRCODE":
                gtin = _extract_gtin_from_qr(data)
                if gtin:
                    logger.info(f"GTIN extracted from QR: {gtin}")
                    return gtin

        return None

    except ImportError:
        logger.warning("pyzbar not installed — barcode detection unavailable")
        return None
    except Exception as e:
        logger.error(f"Barcode detection failed: {type(e).__name__}: {e}")
        return None


def _extract_gtin_from_qr(data: str) -> str | None:
    """Extract a GTIN from a GS1 Digital Link QR code.

    GS1 format: https://id.gs1.org/01/05901234123457
    Also handles: https://example.com/01/05901234123457
    """
    # Match /01/ followed by 8-14 digits (GTIN-8 to GTIN-14)
    match = re.search(r"/01/(\d{8,14})", data)
    if match:
        return match.group(1)

    # Pure numeric QR that looks like a barcode (8-14 digits only)
    if re.fullmatch(r"\d{8,14}", data):
        return data

    return None
