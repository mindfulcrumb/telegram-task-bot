"""Transaction categorization engine with rules and AI fallback."""

from __future__ import annotations

import re
import json
import logging
from pathlib import Path

from bot.accounting.models import Transaction, CategoryRule
from bot.accounting import storage as db

logger = logging.getLogger(__name__)

_CATEGORIES: dict[str, str] = {}


def _load_categories():
    global _CATEGORIES
    if _CATEGORIES:
        return
    rules_path = Path(__file__).parent / "default_rules.json"
    if rules_path.exists():
        data = json.loads(rules_path.read_text())
        _CATEGORIES = data.get("categories", {})


def get_categories() -> dict[str, str]:
    _load_categories()
    return _CATEGORIES.copy()


def get_category_display(key: str) -> str:
    _load_categories()
    return _CATEGORIES.get(key, key)


def _match_rule(description: str, rule: CategoryRule) -> bool:
    desc_lower = description.lower()
    pattern_lower = rule.pattern.lower()

    if rule.match_type == "exact":
        return desc_lower == pattern_lower
    elif rule.match_type == "contains":
        return pattern_lower in desc_lower
    elif rule.match_type == "regex":
        try:
            return bool(re.search(rule.pattern, description, re.IGNORECASE))
        except re.error:
            return False
    return False


def categorize_transaction(transaction: Transaction, rules: list[CategoryRule] | None = None) -> Transaction:
    if rules is None:
        rules = db.get_all_rules()

    for rule in rules:
        if _match_rule(transaction.description, rule):
            transaction.category = rule.category
            transaction.note = rule.note_template
            transaction.confidence = "rule"
            if rule.id is not None:
                db.increment_rule_match(rule.id)
            return transaction

    transaction.confidence = "unknown"
    return transaction


def categorize_batch(transactions: list[Transaction]) -> tuple[list[Transaction], list[Transaction]]:
    rules = db.get_all_rules()
    categorized = []
    uncategorized = []

    for txn in transactions:
        categorize_transaction(txn, rules)
        if txn.confidence != "unknown":
            categorized.append(txn)
        else:
            uncategorized.append(txn)

    logger.info(f"Batch: {len(categorized)} auto-categorized, {len(uncategorized)} need review")
    return categorized, uncategorized


def apply_user_category(transaction: Transaction, category: str, note: str = "", save_rule: bool = True) -> Transaction:
    transaction.category = category
    transaction.note = note or get_category_display(category)
    transaction.confidence = "user"

    if save_rule and transaction.description:
        existing = db.get_all_rules()
        already_exists = any(r.pattern.lower() == transaction.description.lower() for r in existing)
        if not already_exists:
            db.add_rule(
                pattern=transaction.description,
                category=category,
                note=note or get_category_display(category),
                match_type="contains",
            )

    return transaction
