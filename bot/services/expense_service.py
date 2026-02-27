"""Expense tracking — log, query, and summarize spending."""
import logging
from datetime import date, timedelta
from decimal import Decimal

from bot.db.database import get_cursor

logger = logging.getLogger(__name__)


def log_expense(
    user_id: int,
    amount: float,
    currency: str = "EUR",
    category: str = "other",
    description: str = "",
    expense_date: date = None,
) -> dict | None:
    """Log an expense. Returns the created record."""
    if expense_date is None:
        expense_date = date.today()

    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO expenses (user_id, amount, currency, category, description, expense_date)
               VALUES (%s, %s, %s, %s, %s, %s)
               RETURNING id, amount, currency, category, description, expense_date""",
            (user_id, amount, currency.upper(), category.lower(), description, expense_date),
        )
        row = cur.fetchone()
        if row:
            result = dict(row)
            # Convert Decimal to float for JSON serialization
            if isinstance(result.get("amount"), Decimal):
                result["amount"] = float(result["amount"])
            if hasattr(result.get("expense_date"), "isoformat"):
                result["expense_date"] = result["expense_date"].isoformat()
            return result
        return None


def get_expenses(user_id: int, days: int = 30) -> list[dict]:
    """Get recent expenses."""
    cutoff = date.today() - timedelta(days=days)
    with get_cursor() as cur:
        cur.execute(
            """SELECT id, amount, currency, category, description, expense_date
               FROM expenses
               WHERE user_id = %s AND expense_date >= %s
               ORDER BY expense_date DESC, created_at DESC
               LIMIT 50""",
            (user_id, cutoff),
        )
        rows = cur.fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("amount"), Decimal):
                d["amount"] = float(d["amount"])
            if hasattr(d.get("expense_date"), "isoformat"):
                d["expense_date"] = d["expense_date"].isoformat()
            results.append(d)
        return results


def get_spending_summary(user_id: int, days: int = 30) -> dict:
    """Get spending grouped by category."""
    cutoff = date.today() - timedelta(days=days)
    with get_cursor() as cur:
        cur.execute(
            """SELECT category, currency, SUM(amount) as total, COUNT(*) as count
               FROM expenses
               WHERE user_id = %s AND expense_date >= %s
               GROUP BY category, currency
               ORDER BY total DESC""",
            (user_id, cutoff),
        )
        rows = cur.fetchall()
        categories = []
        grand_total = 0.0
        for r in rows:
            total = float(r["total"]) if isinstance(r["total"], Decimal) else r["total"]
            categories.append({
                "category": r["category"],
                "currency": r["currency"],
                "total": total,
                "count": r["count"],
            })
            grand_total += total

        # Daily average
        daily_avg = round(grand_total / max(days, 1), 2)

        return {
            "period_days": days,
            "categories": categories,
            "grand_total": round(grand_total, 2),
            "daily_average": daily_avg,
            "transaction_count": sum(c["count"] for c in categories),
        }


def get_daily_total(user_id: int, target_date: date = None) -> float:
    """Get total spending for a single day."""
    if target_date is None:
        target_date = date.today()
    with get_cursor() as cur:
        cur.execute(
            """SELECT COALESCE(SUM(amount), 0) as total
               FROM expenses
               WHERE user_id = %s AND expense_date = %s""",
            (user_id, target_date),
        )
        row = cur.fetchone()
        total = row["total"] if row else 0
        return float(total) if isinstance(total, Decimal) else total
