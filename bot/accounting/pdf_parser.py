"""PDF parser for TOConline bank reconciliation reports.

Handles the standard TOConline reconciliation format with sections:
1. Saldo do extrato bancario (bank statement balance)
2. Movimentos a debito no Banco (debit movements)
3. Movimentos a credito no Banco (credit movements)
4. Movimentos a debito pela Empresa (company debit movements)
5. Movimentos a credito pela Empresa (company credit movements)
6-8. Reconciled balances and differences

Uses two extraction strategies:
1. Table-based extraction via pdfplumber (for well-structured PDFs)
2. Text-based line parsing with regex (fallback for PDFs without table structures)
"""

from __future__ import annotations

import re
import logging
from pathlib import Path

import pdfplumber

from bot.accounting.models import Transaction, ReconciliationResult

logger = logging.getLogger(__name__)


def parse_portuguese_number(text: str) -> float | None:
    """Parse Portuguese number format: 1.000,29 -> 1000.29"""
    if not text or not text.strip():
        return None
    text = text.strip()
    # Handle negative numbers
    negative = text.startswith("-") or text.startswith("(")
    cleaned = re.sub(r"[^\d.,-]", "", text)
    if not cleaned:
        return None
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        val = float(cleaned)
        return -val if negative and val > 0 else val
    except ValueError:
        return None


def _detect_section(text: str) -> int | None:
    """Detect which section a header row belongs to."""
    if not text:
        return None
    text = text.strip().lower()
    patterns = {
        1: r"1\s*-\s*saldo do extrato",
        2: r"2\s*-\s*movimentos a d[eé]bito no banco",
        3: r"3\s*-\s*movimentos a cr[eé]dito no banco",
        4: r"4\s*-\s*movimentos a d[eé]bito pela empresa",
        5: r"5\s*-\s*movimentos a cr[eé]dito pela empresa",
        6: r"6\s*-\s*saldo do banco reconciliado",
        7: r"7\s*-\s*saldo da conta corrente",
        8: r"8\s*-\s*diferen[cç]a",
    }
    for section_num, pattern in patterns.items():
        if re.search(pattern, text):
            return section_num
    return None


def _is_data_row(row: list) -> bool:
    if not row or not row[0]:
        return False
    first_cell = str(row[0]).strip()
    return bool(re.match(r"\d{4}-\d{2}-\d{2}|\d{2}[-/]\d{2}[-/]\d{4}", first_cell))


def _is_total_row(row: list) -> bool:
    if not row:
        return False
    return any(cell and "total" in str(cell).lower() for cell in row)


def _is_sem_movimentos(row: list) -> bool:
    return any(cell and "sem movimentos" in str(cell).lower() for cell in row if cell)


def _extract_transactions_from_table(
    rows: list[list], section_type: str, start_index: int = 0
) -> list[Transaction]:
    transactions = []
    idx = start_index

    for row in rows:
        if _is_total_row(row) or _is_sem_movimentos(row):
            continue
        if not _is_data_row(row):
            continue

        date_mov = str(row[0]).strip() if row[0] else ""
        description = str(row[2]).strip() if len(row) > 2 and row[2] else ""
        notes = str(row[3]).strip() if len(row) > 3 and row[3] else ""
        value_str = str(row[-1]).strip() if row[-1] else ""

        value = parse_portuguese_number(value_str)
        if value is None:
            logger.warning(f"Could not parse value '{value_str}' for row: {row}")
            continue

        transactions.append(
            Transaction(
                date=date_mov,
                description=description,
                value=value,
                type=section_type,
                row_index=idx,
                original_notes=notes if notes and notes.lower() != "none" else "",
            )
        )
        idx += 1

    return transactions


def _extract_balance_value(text: str) -> float | None:
    match = re.search(r"[-]?\s*[\d.,]+\s*$", text.strip())
    if match:
        return parse_portuguese_number(match.group())
    return None


# --- Text-based fallback parser ---

# Regex for a transaction line: date date description value
# TOConline format: "2025-05-16 2025-05-16 To Carolina Padilha 35,00"
# Or: "2025-05-16 2025-05-16 Lionesa Services - S 0,60"
_TXN_LINE_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+"  # Date mov
    r"(\d{4}-\d{2}-\d{2})\s+"   # Date value
    r"(.+?)\s+"                  # Description (non-greedy)
    r"([\d.,]+)\s*$"             # Value at end of line
)

# Alternative pattern where value has thousands separator
_TXN_LINE_PATTERN_ALT = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+"  # Date mov
    r"(\d{4}-\d{2}-\d{2})\s+"   # Date value
    r"(.+?)\s+"                  # Description
    r"([\d.]+,\d{2})\s*$"       # Value with comma decimal (e.g. 1.000,29)
)


def _parse_transactions_from_text(text: str, section_type: str, start_index: int = 0) -> list[Transaction]:
    """Parse transactions from raw text using regex line matching."""
    transactions = []
    idx = start_index

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Skip header rows, total rows, page footers
        line_lower = line.lower()
        if any(skip in line_lower for skip in [
            "data mov", "data valor", "descri", "notas", "valor",
            "total", "sem movimentos", "página", "pagina", "emitido por",
            "toconline", "crumb origen", "nif:", "valores em eur",
            "relatório", "relatorio", "reconcilia",
            "movimentos a", "saldo do", "diferen",
        ]):
            continue

        # Try matching transaction patterns
        match = _TXN_LINE_PATTERN_ALT.match(line) or _TXN_LINE_PATTERN.match(line)
        if match:
            date_mov = match.group(1)
            # date_val = match.group(2)  # Usually same as date_mov
            description = match.group(3).strip()
            value_str = match.group(4)

            value = parse_portuguese_number(value_str)
            if value is not None and value > 0:
                transactions.append(
                    Transaction(
                        date=date_mov,
                        description=description,
                        value=value,
                        type=section_type,
                        row_index=idx,
                    )
                )
                idx += 1

    return transactions


def parse_reconciliation_pdf(file_path: str | Path) -> ReconciliationResult:
    """Parse a TOConline bank reconciliation PDF.

    First tries table-based extraction. If that yields no results,
    falls back to text-based line parsing.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"PDF not found: {file_path}")

    result = ReconciliationResult(
        filename=file_path.name,
        bank_balance=None,
        reconciled_balance=None,
        company_balance=None,
        difference=None,
    )

    current_section = None
    row_counter = 0

    with pdfplumber.open(file_path) as pdf:
        # Strategy 1: Try table extraction
        for page_num, page in enumerate(pdf.pages):
            page_text = page.extract_text() or ""

            # Detect sections from text
            for line in page_text.split("\n"):
                detected = _detect_section(line)
                if detected is not None:
                    current_section = detected
                    if current_section == 1:
                        result.bank_balance = _extract_balance_value(line)
                    elif current_section == 6:
                        result.reconciled_balance = _extract_balance_value(line)
                    elif current_section == 7:
                        result.company_balance = _extract_balance_value(line)
                    elif current_section == 8:
                        result.difference = _extract_balance_value(line)

            tables = page.extract_tables()
            if tables:
                for table in tables:
                    if not table or len(table) < 1:
                        continue

                    first_row_text = " ".join(str(c) for c in table[0] if c)
                    table_section = _detect_section(first_row_text)
                    if table_section:
                        current_section = table_section

                    if current_section == 2:
                        txns = _extract_transactions_from_table(table, "debit", row_counter)
                        result.debit_transactions.extend(txns)
                        row_counter += len(txns)
                    elif current_section == 3:
                        txns = _extract_transactions_from_table(table, "credit", row_counter)
                        result.credit_transactions.extend(txns)
                        row_counter += len(txns)
                    elif current_section == 4:
                        txns = _extract_transactions_from_table(table, "debit", row_counter)
                        result.company_debits.extend(txns)
                        row_counter += len(txns)
                    elif current_section == 5:
                        txns = _extract_transactions_from_table(table, "credit", row_counter)
                        result.company_credits.extend(txns)
                        row_counter += len(txns)

    # Strategy 2: If table extraction found nothing, use text-based parsing
    if not result.all_transactions:
        logger.info("Table extraction found no transactions, trying text-based parsing...")
        current_section = None
        row_counter = 0

        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                page_text = page.extract_text() or ""
                if not page_text:
                    continue

                logger.debug(f"Page {page_num + 1} text length: {len(page_text)}")

                # Split text into sections and parse each
                lines = page_text.split("\n")
                section_text_buffer = []

                for line in lines:
                    detected = _detect_section(line)
                    if detected is not None:
                        # Process buffered text for previous section
                        if section_text_buffer and current_section in (2, 3, 4, 5):
                            section_type = "debit" if current_section in (2, 4) else "credit"
                            txns = _parse_transactions_from_text(
                                "\n".join(section_text_buffer), section_type, row_counter
                            )
                            if current_section == 2:
                                result.debit_transactions.extend(txns)
                            elif current_section == 3:
                                result.credit_transactions.extend(txns)
                            elif current_section == 4:
                                result.company_debits.extend(txns)
                            elif current_section == 5:
                                result.company_credits.extend(txns)
                            row_counter += len(txns)

                        current_section = detected
                        section_text_buffer = []

                        # Extract balances
                        if current_section == 1:
                            result.bank_balance = _extract_balance_value(line)
                        elif current_section == 6:
                            result.reconciled_balance = _extract_balance_value(line)
                        elif current_section == 7:
                            result.company_balance = _extract_balance_value(line)
                        elif current_section == 8:
                            result.difference = _extract_balance_value(line)
                    else:
                        section_text_buffer.append(line)

                # Process remaining buffer at end of page
                if section_text_buffer and current_section in (2, 3, 4, 5):
                    section_type = "debit" if current_section in (2, 4) else "credit"
                    txns = _parse_transactions_from_text(
                        "\n".join(section_text_buffer), section_type, row_counter
                    )
                    if current_section == 2:
                        result.debit_transactions.extend(txns)
                    elif current_section == 3:
                        result.credit_transactions.extend(txns)
                    elif current_section == 4:
                        result.company_debits.extend(txns)
                    elif current_section == 5:
                        result.company_credits.extend(txns)
                    row_counter += len(txns)

    # Fallback: extract balances from full text if still missing
    if result.bank_balance is None or result.reconciled_balance is None:
        with pdfplumber.open(file_path) as pdf:
            full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            for line in full_text.split("\n"):
                line_s = line.strip()
                if result.bank_balance is None and "(1)" in line_s:
                    result.bank_balance = parse_portuguese_number(line_s.split("(1)")[-1].strip())
                if result.reconciled_balance is None and "(1+2-3+4-5)" in line_s:
                    result.reconciled_balance = parse_portuguese_number(line_s.split("(1+2-3+4-5)")[-1].strip())
                if result.company_balance is None and "(7)" in line_s:
                    result.company_balance = parse_portuguese_number(line_s.split("(7)")[-1].strip())
                if result.difference is None and "(6-7)" in line_s:
                    result.difference = parse_portuguese_number(line_s.split("(6-7)")[-1].strip())

    total = len(result.all_transactions)
    logger.info(
        f"Parsed {result.filename}: {total} transactions "
        f"({len(result.debit_transactions)} debits, {len(result.credit_transactions)} credits, "
        f"{len(result.company_debits)} company debits, {len(result.company_credits)} company credits)"
    )

    return result
