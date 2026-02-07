"""AI-powered categorization for unknown transactions using Claude."""

from __future__ import annotations

import json
import logging

from anthropic import Anthropic

import config
from bot.accounting.models import Transaction
from bot.accounting.categorizer import get_categories

logger = logging.getLogger(__name__)

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        if not config.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY not configured")
        _client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def categorize_with_ai(transactions: list[Transaction]) -> list[Transaction]:
    """Use Claude to suggest categories for uncategorized transactions."""
    if not transactions or not config.ANTHROPIC_API_KEY:
        return transactions

    categories = get_categories()
    cat_list = "\n".join(f"- {key}: {display}" for key, display in categories.items())

    batch_size = 25
    for i in range(0, len(transactions), batch_size):
        batch = transactions[i : i + batch_size]
        _categorize_batch(batch, cat_list)

    return transactions


def _categorize_batch(transactions: list[Transaction], categories_text: str):
    txn_lines = []
    for idx, txn in enumerate(transactions):
        txn_lines.append(
            f"{idx}. {txn.date} | {txn.description} | "
            f"{'Debito' if txn.type == 'debit' else 'Credito'} {txn.value:.2f} EUR"
        )

    prompt = f"""Analisa estas transacoes bancarias de uma empresa portuguesa e categoriza cada uma.

Categorias disponiveis:
{categories_text}

Transacoes:
{chr(10).join(txn_lines)}

Para cada transacao, responde APENAS com JSON array. Cada elemento deve ter:
- "index": numero da transacao
- "category": chave da categoria (ex: "transportes", "fornecedor")
- "note": nota breve em portugues para o contabilista (max 50 caracteres)

Responde APENAS com o JSON array, sem texto adicional."""

    try:
        client = _get_client()
        response = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()
        if "```" in text:
            json_part = text.split("```")[1]
            if json_part.startswith("json"):
                json_part = json_part[4:]
            text = json_part.strip()

        results = json.loads(text)

        for item in results:
            idx = item.get("index")
            if idx is not None and 0 <= idx < len(transactions):
                transactions[idx].category = item.get("category", "outros")
                transactions[idx].note = item.get("note", "")
                transactions[idx].confidence = "ai"

        logger.info(f"AI categorized {len(results)} transactions")

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse AI response: {e}")
    except Exception as e:
        logger.error(f"AI categorization error: {e}")
