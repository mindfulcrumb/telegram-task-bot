"""Invoice parser using Claude vision API for structured extraction."""

from __future__ import annotations

import base64
import json
import logging
import os
import tempfile
from pathlib import Path

from anthropic import Anthropic

import config
from bot.accounting.invoice_models import Invoice, InvoiceLineItem, IVABreakdown

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """Analyze this invoice image and extract ALL information in the following JSON format.
This is a Portuguese business invoice (fatura). Extract everything accurately.

Return ONLY valid JSON with this exact structure:
{
  "vendor_name": "Company name on the invoice",
  "vendor_nif": "NIF/NIPC number (Portuguese tax ID, 9 digits)",
  "invoice_number": "Invoice/fatura number",
  "invoice_date": "YYYY-MM-DD format",
  "due_date": "YYYY-MM-DD format or empty string",
  "line_items": [
    {
      "description": "Item/service description",
      "quantity": 1.0,
      "unit_price": 10.00,
      "iva_rate": 23.0,
      "iva_amount": 2.30,
      "total": 12.30
    }
  ],
  "iva_breakdown": [
    {"rate": 23.0, "base_amount": 100.00, "iva_amount": 23.00}
  ],
  "subtotal": 100.00,
  "total_iva": 23.00,
  "total": 123.00
}

Rules:
- All monetary values as numbers (not strings)
- IVA rates as percentages (6, 13, 23)
- Dates in YYYY-MM-DD format
- If a field is not visible, use empty string for text or 0.0 for numbers
- For line items, calculate total = quantity * unit_price * (1 + iva_rate/100) if not shown
- Include ALL line items visible on the invoice
- The NIF is usually a 9-digit number near the company address
- Return ONLY the JSON, no markdown formatting or extra text"""

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        if not config.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY not configured")
        _client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _image_to_base64(image_path: str) -> tuple[str, str]:
    """Read image file and return (base64_data, media_type)."""
    path = Path(image_path)
    suffix = path.suffix.lower()
    media_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    media_type = media_types.get(suffix, "image/png")
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return data, media_type


def _pdf_to_images(pdf_path: str) -> list[str]:
    """Convert PDF pages to temporary PNG files. Returns list of paths."""
    try:
        from pdf2image import convert_from_path
        images = convert_from_path(pdf_path, dpi=200, fmt="png")
    except ImportError:
        # Fallback: use pdfplumber to extract text and return empty
        # (will be handled by text-based extraction)
        logger.warning("pdf2image not available, falling back to text extraction")
        return []

    paths = []
    for i, img in enumerate(images[:4]):  # Limit to 4 pages
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img.save(tmp.name, "PNG")
        paths.append(tmp.name)
    return paths


def _extract_with_text_fallback(pdf_path: str) -> Invoice:
    """Fallback: extract text with pdfplumber and send to Claude as text."""
    import pdfplumber

    text_content = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[:4]:
            text_content += (page.extract_text() or "") + "\n\n"

    if not text_content.strip():
        raise ValueError("Could not extract any text from PDF")

    client = _get_client()
    prompt = EXTRACTION_PROMPT.replace("this invoice image", "this invoice text")

    response = client.messages.create(
        model=getattr(config, "CLAUDE_MODEL", "claude-sonnet-4-5-20250929"),
        max_tokens=4096,
        messages=[{"role": "user", "content": f"{prompt}\n\nINVOICE TEXT:\n{text_content}"}],
    )

    return _parse_response(response)


def parse_invoice_image(image_path: str) -> Invoice:
    """Parse an invoice from a single image using Claude vision."""
    client = _get_client()
    b64_data, media_type = _image_to_base64(image_path)

    response = client.messages.create(
        model=getattr(config, "CLAUDE_MODEL", "claude-sonnet-4-5-20250929"),
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64_data,
                    },
                },
                {"type": "text", "text": EXTRACTION_PROMPT},
            ],
        }],
    )

    return _parse_response(response)


def parse_invoice_pdf(pdf_path: str) -> Invoice:
    """Parse an invoice from a PDF by converting to images."""
    image_paths = _pdf_to_images(pdf_path)

    if not image_paths:
        # pdf2image not available or failed — use text fallback
        return _extract_with_text_fallback(pdf_path)

    try:
        client = _get_client()
        content_blocks = []
        for img_path in image_paths:
            b64_data, media_type = _image_to_base64(img_path)
            content_blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": b64_data,
                },
            })
        content_blocks.append({"type": "text", "text": EXTRACTION_PROMPT})

        response = client.messages.create(
            model=getattr(config, "CLAUDE_MODEL", "claude-sonnet-4-5-20250929"),
            max_tokens=4096,
            messages=[{"role": "user", "content": content_blocks}],
        )

        return _parse_response(response)
    finally:
        for p in image_paths:
            try:
                os.unlink(p)
            except OSError:
                pass


def _parse_response(response) -> Invoice:
    """Parse Claude's response into an Invoice object."""
    text = response.content[0].text.strip()

    # Strip markdown code fences if present
    if "```" in text:
        json_part = text.split("```")[1]
        if json_part.startswith("json"):
            json_part = json_part[4:]
        text = json_part.strip()

    data = json.loads(text)

    line_items = [
        InvoiceLineItem(
            description=item.get("description", ""),
            quantity=float(item.get("quantity", 1)),
            unit_price=float(item.get("unit_price", 0)),
            iva_rate=float(item.get("iva_rate", 0)),
            iva_amount=float(item.get("iva_amount", 0)),
            total=float(item.get("total", 0)),
            line_index=i,
        )
        for i, item in enumerate(data.get("line_items", []))
    ]

    iva_breakdown = [
        IVABreakdown(
            rate=float(b.get("rate", 0)),
            base_amount=float(b.get("base_amount", 0)),
            iva_amount=float(b.get("iva_amount", 0)),
        )
        for b in data.get("iva_breakdown", [])
    ]

    return Invoice(
        vendor_name=data.get("vendor_name", ""),
        vendor_nif=data.get("vendor_nif", ""),
        invoice_number=data.get("invoice_number", ""),
        invoice_date=data.get("invoice_date", ""),
        due_date=data.get("due_date", ""),
        line_items=line_items,
        iva_breakdown=iva_breakdown,
        subtotal=float(data.get("subtotal", 0)),
        total_iva=float(data.get("total_iva", 0)),
        total=float(data.get("total", 0)),
    )
