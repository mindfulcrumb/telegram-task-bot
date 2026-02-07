"""Telegram handlers for accounting reconciliation.

Adds PDF upload, auto-categorization, interactive review, and export
to the existing task bot.
"""

from __future__ import annotations

import os
import uuid
import logging
import tempfile
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


# --- Document Upload ---


async def handle_pdf_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded PDF documents for reconciliation."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Acesso nao autorizado.")
        return

    document = update.message.document
    if not document.file_name.lower().endswith(".pdf"):
        return  # Not a PDF, let other handlers deal with it

    await update.message.reply_text("A processar o PDF de reconciliacao... Um momento.")

    try:
        file = await context.bot.get_file(document.file_id)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            await file.download_to_drive(tmp.name)
            tmp_path = tmp.name

        result = parse_reconciliation_pdf(tmp_path)
        os.unlink(tmp_path)

        if not result.all_transactions:
            await update.message.reply_text(
                "Nao encontrei transacoes neste PDF. "
                "Verifica se e um relatorio de reconciliacao do TOConline."
            )
            return

        # Categorize with rules
        all_txns = result.all_transactions
        categorized, uncategorized = categorize_batch(all_txns)

        # AI categorization for unknowns
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

        # Save session to memory AND SQLite (persists across bot restarts)
        session_id = str(uuid.uuid4())[:8]
        acct_db.save_full_session(session_id, document.file_name, result)

        context.user_data["acct_session"] = {
            "id": session_id,
            "filename": document.file_name,
            "result": result,
            "pending_review": still_unknown,
            "current_index": 0,
            "reviewed_count": 0,
        }

        summary = (
            f"PDF processado: {document.file_name}\n\n"
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

    except Exception as e:
        logger.error(f"Error processing PDF: {e}", exc_info=True)
        await update.message.reply_text(f"Erro ao processar o PDF: {str(e)}")


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


def get_session_context(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Get accounting session context string for the AI brain. Returns None if no active session."""
    session = _try_restore_session(context)
    if not session:
        return None

    result = session["result"]
    pending = session.get("pending_review", [])
    reviewed = session.get("reviewed_count", 0)
    current_idx = session.get("current_index", 0)
    remaining = max(0, len(pending) - current_idx)

    text = (
        f"ACTIVE ACCOUNTING SESSION:\n"
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
    return text
