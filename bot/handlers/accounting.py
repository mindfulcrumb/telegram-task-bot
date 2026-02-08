"""Telegram handlers for accounting reconciliation and invoice scanning.

Adds PDF upload, photo upload, auto-categorization, interactive review, and export
to the existing task bot.
"""

from __future__ import annotations

import os
import uuid
import logging
import tempfile
from datetime import datetime
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import config
from bot.accounting.pdf_parser import parse_reconciliation_pdf
from bot.accounting.categorizer import (
    categorize_batch,
    apply_user_category,
    get_categories,
    get_category_display,
)
from bot.accounting.export_service import export_excel, export_csv, export_pdf
from bot.accounting.ai_categorizer import categorize_with_ai
from bot.accounting import storage as acct_db

logger = logging.getLogger(__name__)


def is_authorized(user_id: int) -> bool:
    if not config.ALLOWED_USER_IDS:
        return True
    return user_id in config.ALLOWED_USER_IDS


# --- Keyboards ---


def _category_keyboard(txn_index: int) -> InlineKeyboardMarkup:
    categories = get_categories()
    common = [
        "fornecedor", "transportes", "alimentacao", "software",
        "combustivel", "viagens", "material_escritorio", "marketing",
        "servicos_profissionais", "saude", "aluguer", "suprimentos",
        "transferencia", "receita_vendas", "outros",
    ]
    buttons = []
    row = []
    for cat_key in common:
        if cat_key in categories:
            label = categories[cat_key][:20]
            row.append(InlineKeyboardButton(label, callback_data=f"acct:{txn_index}:{cat_key}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("Saltar", callback_data=f"acct:{txn_index}:skip")])
    return InlineKeyboardMarkup(buttons)


def _export_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Excel (.xlsx)", callback_data="acct_export:excel"),
            InlineKeyboardButton("CSV (.csv)", callback_data="acct_export:csv"),
        ],
        [
            InlineKeyboardButton("PDF (.pdf)", callback_data="acct_export:pdf"),
        ],
    ])


# --- Commands ---


async def cmd_reconcile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /reconcile - show status or instructions."""
    if not is_authorized(update.effective_user.id):
        return

    session = _try_restore_session(context)
    if session and session.get("result"):
        result = session["result"]
        pending = session.get("pending_review", [])
        reviewed = session.get("reviewed_count", 0)
        await update.message.reply_text(
            f"Sessao ativa: {session.get('filename', '?')}\n"
            f"Total: {len(result.all_transactions)} transacoes\n"
            f"Revisao: {reviewed} feitas, {len(pending) - session.get('current_index', 0)} restantes\n\n"
            "Usa /acct_export para exportar ou envia outro PDF para comecar de novo."
        )
    else:
        await update.message.reply_text(
            "Envia-me um PDF de reconciliacao bancaria (TOConline) e eu vou:\n"
            "1. Extrair todas as transacoes\n"
            "2. Categorizar automaticamente as conhecidas\n"
            "3. Pedir-te ajuda com as restantes\n"
            "4. Gerar um Excel/CSV anotado\n\n"
            "Basta enviar o ficheiro PDF aqui no chat!"
        )


async def cmd_acct_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /acct_categories - list available categories."""
    if not is_authorized(update.effective_user.id):
        return

    categories = get_categories()
    lines = ["Categorias disponiveis:\n"]
    for key, display in sorted(categories.items(), key=lambda x: x[1]):
        lines.append(f"  {display}")
    await update.message.reply_text("\n".join(lines))


async def cmd_acct_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /acct_export - export current session."""
    if not is_authorized(update.effective_user.id):
        return

    session = _try_restore_session(context)
    if not session or not session.get("result"):
        await update.message.reply_text("Nenhuma sessao ativa. Envia um PDF primeiro.")
        return

    await update.message.reply_text("Escolhe o formato:", reply_markup=_export_keyboard())


async def cmd_acct_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /acct_skip - skip current transaction in review."""
    if not is_authorized(update.effective_user.id):
        return

    session = _try_restore_session(context)
    if not session or not session.get("pending_review"):
        await update.message.reply_text("Nenhuma transacao para saltar.")
        return

    session["current_index"] = session.get("current_index", 0) + 1
    await _send_next_review(update, context)


# --- PDF Type Detection ---


def _detect_pdf_type(pdf_path: str) -> str:
    """Detect if PDF is a bank reconciliation or an invoice."""
    import pdfplumber
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                return "invoice"
            text = (pdf.pages[0].extract_text() or "").lower()
            reconciliation_markers = [
                "reconcilia", "saldo do extrato", "toconline",
                "movimentos a debito no banco", "movimentos a credito no banco",
            ]
            if any(marker in text for marker in reconciliation_markers):
                return "reconciliation"
    except Exception:
        pass
    return "invoice"


# --- Document Upload ---


async def handle_pdf_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded PDF documents - detect type and route accordingly."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Acesso nao autorizado.")
        return

    document = update.message.document
    if not document.file_name.lower().endswith(".pdf"):
        return

    tmp_path = None
    try:
        file = await context.bot.get_file(document.file_id)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            await file.download_to_drive(tmp.name)
            tmp_path = tmp.name

        pdf_type = _detect_pdf_type(tmp_path)

        if pdf_type == "reconciliation":
            await _handle_reconciliation_pdf(update, context, tmp_path, document.file_name)
        else:
            await _handle_invoice_pdf(update, context, tmp_path, document.file_name)

    except Exception as e:
        logger.error(f"Error processing PDF: {e}", exc_info=True)
        await update.message.reply_text(f"Erro ao processar o PDF: {str(e)}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


async def _handle_reconciliation_pdf(update, context, tmp_path, filename):
    """Process a bank reconciliation PDF (TOConline format)."""
    await update.message.reply_text("A processar o PDF de reconciliacao... Um momento.")

    result = parse_reconciliation_pdf(tmp_path)

    if not result.all_transactions:
        await update.message.reply_text(
            "Nao encontrei transacoes neste PDF. "
            "Verifica se e um relatorio de reconciliacao do TOConline."
        )
        return

    all_txns = result.all_transactions
    categorized, uncategorized = categorize_batch(all_txns)

    if uncategorized and config.ANTHROPIC_API_KEY:
        await update.message.reply_text(
            f"A pedir ajuda a IA para {len(uncategorized)} transacoes..."
        )
        categorize_with_ai(uncategorized)
        still_unknown = [t for t in uncategorized if t.confidence == "unknown"]
        ai_done = [t for t in uncategorized if t.confidence == "ai"]
    else:
        still_unknown = uncategorized
        ai_done = []

    session_id = str(uuid.uuid4())[:8]
    acct_db.save_full_session(session_id, filename, result)

    context.user_data["acct_session"] = {
        "id": session_id,
        "filename": filename,
        "result": result,
        "pending_review": still_unknown,
        "current_index": 0,
        "reviewed_count": 0,
    }

    summary = (
        f"PDF processado: {filename}\n\n"
        f"Total transacoes: {len(all_txns)}\n"
        f"  Debitos banco: {len(result.debit_transactions)}\n"
        f"  Creditos banco: {len(result.credit_transactions)}\n"
        f"  Debitos empresa: {len(result.company_debits)}\n"
        f"  Creditos empresa: {len(result.company_credits)}\n\n"
        f"Auto-categorizadas (regras): {len(categorized)}\n"
        f"Categorizadas por IA: {len(ai_done)}\n"
        f"Precisam revisao manual: {len(still_unknown)}"
    )
    if result.bank_balance is not None:
        summary += f"\n\nSaldo bancario: {result.bank_balance:.2f} EUR"
    if result.difference is not None:
        summary += f"\nDiferenca: {result.difference:.2f} EUR"

    await update.message.reply_text(summary)

    if still_unknown:
        await update.message.reply_text(
            "Vou mostrar-te as transacoes nao categorizadas. Escolhe a categoria:"
        )
        await _send_next_review(update, context)
    else:
        await update.message.reply_text(
            "Todas as transacoes foram categorizadas!\n"
            "Usa /acct_export para gerar o ficheiro.",
            reply_markup=_export_keyboard(),
        )


async def _handle_invoice_pdf(update, context, tmp_path, filename):
    """Process an invoice PDF using Claude vision."""
    await update.message.reply_text("A processar a fatura... Analisando com IA.")

    from bot.accounting.invoice_parser import parse_invoice_pdf

    invoice = parse_invoice_pdf(tmp_path)
    invoice.source_type = "pdf"
    invoice.source_filename = filename
    invoice.session_id = str(uuid.uuid4())[:8]

    _auto_categorize_invoice(invoice)

    invoice.id = acct_db.save_invoice(invoice)

    context.user_data["invoice_session"] = {
        "invoice": invoice,
        "id": invoice.session_id,
    }

    summary = _format_invoice_summary(invoice)
    await update.message.reply_text(summary)


# --- Photo Upload (Invoices) ---


async def handle_photo_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded photos - assumed to be invoices."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Acesso nao autorizado.")
        return

    photo = update.message.photo[-1]  # highest resolution

    await update.message.reply_text("A processar a foto da fatura... Analisando com IA.")

    tmp_path = None
    try:
        file = await context.bot.get_file(photo.file_id)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            await file.download_to_drive(tmp.name)
            tmp_path = tmp.name

        from bot.accounting.invoice_parser import parse_invoice_image

        invoice = parse_invoice_image(tmp_path)
        invoice.source_type = "photo"
        invoice.source_filename = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        invoice.session_id = str(uuid.uuid4())[:8]

        _auto_categorize_invoice(invoice)

        invoice.id = acct_db.save_invoice(invoice)

        context.user_data["invoice_session"] = {
            "invoice": invoice,
            "id": invoice.session_id,
        }

        summary = _format_invoice_summary(invoice)
        await update.message.reply_text(summary)

    except Exception as e:
        logger.error(f"Error processing photo invoice: {e}", exc_info=True)
        await update.message.reply_text(f"Erro ao processar a foto: {str(e)}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# --- Interactive Review ---


async def _send_next_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = _try_restore_session(context)
    if not session:
        return

    pending = session.get("pending_review", [])
    idx = session.get("current_index", 0)

    if idx >= len(pending):
        await update.effective_chat.send_message(
            f"Revisao completa! {session.get('reviewed_count', 0)} transacoes revisadas.\n"
            "Usa /acct_export para gerar o ficheiro.",
            reply_markup=_export_keyboard(),
        )
        return

    txn = pending[idx]
    remaining = len(pending) - idx

    text = (
        f"[{idx + 1}/{len(pending)}] Transacao para categorizar:\n\n"
        f"Data: {txn.date}\n"
        f"Descricao: {txn.description}\n"
        f"Valor: {txn.value:.2f} EUR\n"
        f"Tipo: {'Debito' if txn.type == 'debit' else 'Credito'}\n\n"
        f"Restantes: {remaining}"
    )
    await update.effective_chat.send_message(text, reply_markup=_category_keyboard(idx))


# --- Callback Handlers ---


async def handle_acct_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard presses for accounting."""
    query = update.callback_query
    await query.answer()

    if not is_authorized(query.from_user.id):
        return

    data = query.data

    if data.startswith("acct:"):
        await _handle_category_callback(update, context, data)
    elif data.startswith("acct_export:"):
        await _handle_export_callback(update, context, data)


async def _handle_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query
    parts = data.split(":")
    if len(parts) != 3:
        return

    _, txn_idx_str, category = parts
    txn_idx = int(txn_idx_str)

    session = _try_restore_session(context)
    if not session:
        await query.edit_message_text("Sessao expirada. Envia um novo PDF.")
        return

    pending = session.get("pending_review", [])
    if txn_idx >= len(pending):
        return

    txn = pending[txn_idx]

    if category == "skip":
        await query.edit_message_text(f"Saltada: {txn.description} ({txn.value:.2f} EUR)")
    else:
        apply_user_category(txn, category, save_rule=True)
        display = get_category_display(category)
        await query.edit_message_text(
            f"Categorizada: {txn.description}\n  -> {display}\n  Regra guardada."
        )
        # Persist category change to SQLite
        acct_db.update_transaction_category(
            session["id"], txn.date, txn.description, txn.value,
            txn.category or category, txn.note or "",
        )

    session["current_index"] = txn_idx + 1
    session["reviewed_count"] = session.get("reviewed_count", 0) + 1
    # Persist review progress
    acct_db.update_session_review_state(
        session["id"], session["current_index"], session["reviewed_count"]
    )
    await _send_next_review(update, context)


async def _handle_export_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query
    fmt = data.split(":")[1]

    session = _try_restore_session(context)
    if not session or not session.get("result"):
        await query.edit_message_text("Sessao expirada. Envia um novo PDF.")
        return

    result = session["result"]
    filename_base = Path(session["filename"]).stem

    await query.edit_message_text(f"A gerar {fmt.upper()}...")

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            if fmt == "excel":
                out = Path(tmp_dir) / f"{filename_base}_categorizado.xlsx"
                export_excel(result, out)
            elif fmt == "pdf":
                out = Path(tmp_dir) / f"{filename_base}_categorizado.pdf"
                export_pdf(result, out)
            else:
                out = Path(tmp_dir) / f"{filename_base}_categorizado.csv"
                export_csv(result, out)

            with open(out, "rb") as f:
                await update.effective_chat.send_document(
                    document=f, filename=out.name,
                    caption=f"Reconciliacao: {session['filename']} ({len(result.all_transactions)} transacoes)",
                )

        acct_db.complete_session(session["id"])
        for txn in result.all_transactions:
            acct_db.save_transaction(
                session["id"], txn.date, txn.description, txn.value,
                txn.type, txn.category or "sem_categoria", txn.note or "", txn.confidence,
            )

    except Exception as e:
        logger.error(f"Export error: {e}", exc_info=True)
        await update.effective_chat.send_message(f"Erro ao exportar: {str(e)}")


# --- Helper functions for AI-routed accounting messages ---


async def handle_accounting_export(update: Update, context: ContextTypes.DEFAULT_TYPE, fmt: str):
    """Export current session in the given format. Called from AI brain or text handler."""
    session = _try_restore_session(context)
    if not session or not session.get("result"):
        await update.message.reply_text("Nenhuma sessao ativa. Envia um PDF primeiro.")
        return

    result = session["result"]
    filename_base = Path(session["filename"]).stem

    await update.message.reply_text(f"A gerar {fmt.upper()}...")

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            if fmt == "excel":
                out = Path(tmp_dir) / f"{filename_base}_categorizado.xlsx"
                export_excel(result, out)
            elif fmt == "pdf":
                out = Path(tmp_dir) / f"{filename_base}_categorizado.pdf"
                export_pdf(result, out)
            else:
                out = Path(tmp_dir) / f"{filename_base}_categorizado.csv"
                export_csv(result, out)

            with open(out, "rb") as f:
                await update.effective_chat.send_document(
                    document=f, filename=out.name,
                    caption=f"Reconciliacao: {session['filename']} ({len(result.all_transactions)} transacoes)",
                )

        acct_db.complete_session(session["id"])

    except Exception as e:
        logger.error(f"Export error: {e}", exc_info=True)
        await update.message.reply_text(f"Erro ao exportar: {str(e)}")


async def handle_accounting_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current accounting session status. Called from AI brain."""
    session = _try_restore_session(context)
    if not session or not session.get("result"):
        await update.message.reply_text("Nenhuma sessao ativa. Envia um PDF primeiro.")
        return

    result = session["result"]
    pending = session.get("pending_review", [])
    reviewed = session.get("reviewed_count", 0)
    current_idx = session.get("current_index", 0)
    remaining = max(0, len(pending) - current_idx)

    text = (
        f"Sessao ativa: {session.get('filename', '?')}\n\n"
        f"Total transacoes: {len(result.all_transactions)}\n"
        f"  Debitos banco: {len(result.debit_transactions)}\n"
        f"  Creditos banco: {len(result.credit_transactions)}\n"
        f"  Debitos empresa: {len(result.company_debits)}\n"
        f"  Creditos empresa: {len(result.company_credits)}\n\n"
        f"Categorizadas: {len(result.categorized)}\n"
        f"Revisadas manualmente: {reviewed}\n"
        f"Restantes para revisao: {remaining}\n"
    )
    if result.bank_balance is not None:
        text += f"\nSaldo bancario: {result.bank_balance:.2f} EUR"
    if result.difference is not None:
        text += f"\nDiferenca: {result.difference:.2f} EUR"

    text += "\n\nUsa /acct_export ou diz 'exportar' para gerar o ficheiro."
    await update.message.reply_text(text)


async def handle_accounting_update(update: Update, context: ContextTypes.DEFAULT_TYPE, transactions_data: list[dict]):
    """Update category/note for one or more transactions. Called from AI brain."""
    session = _try_restore_session(context)
    if not session or not session.get("result"):
        await update.message.reply_text("Nenhuma sessao ativa. Envia um PDF primeiro.")
        return

    result = session["result"]
    all_txns = result.all_transactions
    categories = get_categories()
    updated = []

    for update_item in transactions_data:
        search_desc = (update_item.get("description") or "").lower().strip()
        new_category = update_item.get("category", "")
        new_note = update_item.get("note", "")

        if not search_desc:
            continue

        # Find matching transactions by partial description match
        matches = []
        for txn in all_txns:
            if search_desc in txn.description.lower():
                matches.append(txn)

        for txn in matches:
            if new_category:
                txn.category = new_category
                txn.confidence = "user"
            if new_note:
                txn.note = new_note
            if not new_note and new_category:
                # Auto-generate note from category display name
                txn.note = categories.get(new_category, new_category)

            # Persist to SQLite
            acct_db.update_transaction_category(
                session["id"], txn.date, txn.description, txn.value,
                txn.category or "", txn.note or "",
            )
            updated.append(f"{txn.description[:30]} -> {categories.get(new_category, new_category)}")

    # Also persist full session to SQLite so changes survive restarts
    if updated:
        acct_db.save_full_session(
            session["id"], session["filename"], result,
            session.get("current_index", 0), session.get("reviewed_count", 0),
        )

    return updated


def _try_restore_session(context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    """Try to restore an active session from SQLite if not in memory."""
    session = context.user_data.get("acct_session")
    if session and session.get("result"):
        return session

    # Try SQLite
    restored = acct_db.load_latest_session()
    if restored:
        context.user_data["acct_session"] = restored
        logger.info(f"Restored accounting session {restored['id']} from SQLite")
        return restored
    return None


# --- Invoice Helpers ---


def _auto_categorize_invoice(invoice):
    """Try to categorize invoice using vendor name against existing rules."""
    rules = acct_db.get_all_rules()
    vendor_lower = invoice.vendor_name.lower()

    for rule in rules:
        pattern_lower = rule.pattern.lower()
        matched = False
        if rule.match_type == "contains" and pattern_lower in vendor_lower:
            matched = True
        elif rule.match_type == "exact" and pattern_lower == vendor_lower:
            matched = True

        if matched:
            invoice.category = rule.category
            invoice.note = rule.note_template
            invoice.confidence = "rule"
            if rule.id is not None:
                acct_db.increment_rule_match(rule.id)
            return

    # AI categorization fallback
    if config.ANTHROPIC_API_KEY:
        _ai_categorize_invoice(invoice)


def _ai_categorize_invoice(invoice):
    """Use Claude to suggest a category for an invoice."""
    import json
    from anthropic import Anthropic

    categories = get_categories()
    cat_list = "\n".join(f"- {key}: {display}" for key, display in categories.items())

    items_text = "\n".join(
        f"  - {item.description} ({item.total:.2f} EUR)"
        for item in invoice.line_items
    ) if invoice.line_items else "  (no line items)"

    prompt = f"""Categorize this invoice from a Portuguese business.

Vendor: {invoice.vendor_name}
NIF: {invoice.vendor_nif}
Total: {invoice.total:.2f} EUR
Items:
{items_text}

Available categories:
{cat_list}

Return ONLY a JSON object: {{"category": "key", "note": "brief note in Portuguese (max 50 chars)"}}"""

    try:
        client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=getattr(config, "CLAUDE_MODEL", "claude-sonnet-4-5-20250929"),
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if "```" in text:
            json_part = text.split("```")[1]
            if json_part.startswith("json"):
                json_part = json_part[4:]
            text = json_part.strip()

        result = json.loads(text)
        invoice.category = result.get("category", "outros")
        invoice.note = result.get("note", "")
        invoice.confidence = "ai"
        logger.info(f"AI categorized invoice: {invoice.vendor_name} -> {invoice.category}")
    except Exception as e:
        logger.error(f"AI invoice categorization failed: {e}")


def _format_invoice_summary(invoice) -> str:
    """Format invoice data for Telegram display."""
    categories = get_categories()
    cat_display = categories.get(invoice.category, "Sem Categoria") if invoice.category else "Sem Categoria"
    confidence_map = {"rule": "Auto (regra)", "ai": "IA", "user": "Manual", "unknown": "Nao categorizado"}

    text = (
        f"Fatura processada!\n\n"
        f"Fornecedor: {invoice.vendor_name}\n"
        f"NIF: {invoice.vendor_nif}\n"
        f"Fatura N.: {invoice.invoice_number}\n"
        f"Data: {invoice.invoice_date}\n\n"
    )

    if invoice.line_items:
        text += f"Itens ({len(invoice.line_items)}):\n"
        for item in invoice.line_items:
            text += f"  - {item.description}: {item.total:.2f} EUR (IVA {item.iva_rate}%)\n"
        text += "\n"

    text += f"Subtotal: {invoice.subtotal:.2f} EUR\n"
    text += f"IVA: {invoice.total_iva:.2f} EUR\n"

    if invoice.iva_breakdown:
        for b in invoice.iva_breakdown:
            text += f"  IVA {b.rate}%: {b.iva_amount:.2f} EUR (base: {b.base_amount:.2f})\n"

    text += (
        f"Total: {invoice.total:.2f} EUR\n\n"
        f"Categoria: {cat_display} [{confidence_map.get(invoice.confidence, invoice.confidence)}]\n"
        f"ID: #{invoice.id}\n\n"
        "Podes dizer-me para alterar a categoria, corrigir valores, ou exportar."
    )
    return text


# --- Session Context for AI Brain ---


def get_session_context(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Get accounting/invoice session context for the AI brain."""
    parts = []

    # Reconciliation context
    recon_ctx = _get_reconciliation_context(context)
    if recon_ctx:
        parts.append(recon_ctx)

    # Invoice context
    inv_ctx = _get_invoice_context(context)
    if inv_ctx:
        parts.append(inv_ctx)

    return "\n\n".join(parts) if parts else None


def _get_reconciliation_context(context) -> str | None:
    """Build reconciliation context string."""
    session = _try_restore_session(context)
    if not session:
        return None

    result = session["result"]
    pending = session.get("pending_review", [])
    reviewed = session.get("reviewed_count", 0)
    current_idx = session.get("current_index", 0)
    remaining = max(0, len(pending) - current_idx)

    text = (
        f"ACTIVE RECONCILIATION SESSION:\n"
        f"- File: {session.get('filename', '?')}\n"
        f"- Total transactions: {len(result.all_transactions)}\n"
        f"- Categorized: {len(result.categorized)}\n"
        f"- Manually reviewed: {reviewed}\n"
        f"- Remaining for review: {remaining}"
    )
    if result.bank_balance is not None:
        text += f"\n- Bank balance: {result.bank_balance:.2f} EUR"
    if result.difference is not None:
        text += f"\n- Difference: {result.difference:.2f} EUR"

    categories = get_categories()
    all_txns = result.all_transactions
    text += f"\n\nTRANSACTION LIST ({len(all_txns)} total):\n"
    for i, txn in enumerate(all_txns):
        cat_display = categories.get(txn.category, txn.category) if txn.category else "SEM CATEGORIA"
        note_str = f' | Note: "{txn.note}"' if txn.note else ""
        text += (
            f"  #{i+1}: {txn.date} | {txn.description} | {txn.value:.2f} EUR | "
            f"{'Debito' if txn.type == 'debit' else 'Credito'} | "
            f"Cat: {cat_display}{note_str} | [{txn.confidence}]\n"
        )

    return text


def _get_invoice_context(context) -> str | None:
    """Build invoice context string for the AI brain."""
    session = context.user_data.get("invoice_session")
    if not session:
        return None

    invoice = session["invoice"]
    categories = get_categories()
    cat_display = categories.get(invoice.category, invoice.category) if invoice.category else "SEM CATEGORIA"

    text = (
        f"ACTIVE INVOICE:\n"
        f"- Vendor: {invoice.vendor_name}\n"
        f"- NIF: {invoice.vendor_nif}\n"
        f"- Invoice #: {invoice.invoice_number}\n"
        f"- Date: {invoice.invoice_date}\n"
        f"- Subtotal: {invoice.subtotal:.2f} EUR\n"
        f"- IVA Total: {invoice.total_iva:.2f} EUR\n"
        f"- Total: {invoice.total:.2f} EUR\n"
        f"- Category: {cat_display}\n"
        f"- DB ID: {invoice.id}\n"
    )

    if invoice.line_items:
        text += "\nLINE ITEMS:\n"
        for item in invoice.line_items:
            text += f"  - {item.description}: {item.quantity}x {item.unit_price:.2f} + IVA {item.iva_rate}% = {item.total:.2f} EUR\n"

    if invoice.iva_breakdown:
        text += "\nIVA BREAKDOWN:\n"
        for b in invoice.iva_breakdown:
            text += f"  - {b.rate}%: base {b.base_amount:.2f} | IVA {b.iva_amount:.2f} EUR\n"

    # Also list recent invoices
    recent = acct_db.load_recent_invoices(5)
    if recent:
        text += f"\nRECENT INVOICES ({len(recent)}):\n"
        for inv in recent:
            cat = categories.get(inv.category, inv.category) if inv.category else "?"
            text += f"  ID#{inv.id}: {inv.vendor_name} | {inv.invoice_date} | {inv.total:.2f} EUR | {cat}\n"

    return text
