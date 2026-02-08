"""Tool definitions and executor for the agent loop."""
import json
import logging
import tempfile
from datetime import datetime, date
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# ── Undo buffer (shared state, accessed by tool executor) ────────────────────
_undo_buffer = {}  # {chat_id: [{"action": str, "task_id": str, "title": str}, ...]}

# ── Pending email drafts ─────────────────────────────────────────────────────
_pending_emails = {}  # {chat_id: {"to": str, "subject": str, "body": str}}


# ── Tool Definitions (Claude tool_use schema) ───────────────────────────────

def get_tool_definitions() -> list:
    """Return all available tool definitions based on current config."""
    tools = [
        # ── Task tools ───────────────────────────────────────────────
        {
            "name": "get_tasks",
            "description": "Get the user's current tasks. Use this when they ask to see tasks, what's pending, what's due, etc.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "enum": ["all", "today", "business", "personal", "overdue", "week"],
                        "description": "Filter tasks. 'all' returns everything."
                    }
                },
                "required": ["filter"]
            }
        },
        {
            "name": "add_task",
            "description": "Create a new task. Infer category (Personal/Business) and priority from context.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task title"},
                    "category": {"type": "string", "enum": ["Personal", "Business"], "description": "Task category"},
                    "priority": {"type": "string", "enum": ["Low", "Medium", "High"], "description": "Priority level"},
                    "due_date": {"type": "string", "description": "Due date in YYYY-MM-DD format, or null if none"}
                },
                "required": ["title"]
            }
        },
        {
            "name": "complete_tasks",
            "description": "Mark one or more tasks as done by their task numbers from the list.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_numbers": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "List of task numbers to mark as done"
                    }
                },
                "required": ["task_numbers"]
            }
        },
        {
            "name": "delete_tasks",
            "description": "Delete one or more tasks by their task numbers from the list.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_numbers": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "List of task numbers to delete"
                    }
                },
                "required": ["task_numbers"]
            }
        },
        {
            "name": "undo_last_action",
            "description": "Undo the last delete or done action, restoring the affected tasks.",
            "input_schema": {"type": "object", "properties": {}}
        },
        {
            "name": "edit_task",
            "description": "Edit a task's title.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_number": {"type": "integer", "description": "Task number to edit"},
                    "new_title": {"type": "string", "description": "New title for the task"}
                },
                "required": ["task_number", "new_title"]
            }
        },
    ]

    # ── Email tools (only if configured) ─────────────────────────────
    from bot.services.email_service import is_email_configured
    if is_email_configured():
        tools.extend([
            {
                "name": "send_email",
                "description": "Send an email. Draft the full email body yourself based on what the user wants to say.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient email address(es), comma-separated for multiple"},
                        "subject": {"type": "string", "description": "Email subject line"},
                        "body": {"type": "string", "description": "Full email body text"}
                    },
                    "required": ["to", "subject", "body"]
                }
            },
            {
                "name": "check_inbox",
                "description": "Check recent received emails.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "Number of emails to show (default 10)"}
                    }
                }
            },
            {
                "name": "read_email",
                "description": "Read the full content of a specific email by its number.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "email_number": {"type": "integer", "description": "Email number from the inbox list"}
                    },
                    "required": ["email_number"]
                }
            },
            {
                "name": "reply_to_email",
                "description": "Reply to an email by its number. Draft the reply body based on what the user wants to say.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "email_number": {"type": "integer", "description": "Email number to reply to"},
                        "body": {"type": "string", "description": "Reply body text"}
                    },
                    "required": ["email_number", "body"]
                }
            },
        ])

    # ── WhatsApp (only if configured) ────────────────────────────────
    from bot.services.whatsapp_service import is_whatsapp_configured
    if is_whatsapp_configured():
        tools.append({
            "name": "send_whatsapp",
            "description": "Send a WhatsApp message.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to_number": {"type": "string", "description": "Phone number with country code (e.g., +351912345678)"},
                    "message": {"type": "string", "description": "Message text"}
                },
                "required": ["to_number", "message"]
            }
        })

    # ── Contacts ─────────────────────────────────────────────────────
    tools.extend([
        {
            "name": "lookup_contact",
            "description": "Look up a contact's email or phone number by name. Use this before sending emails or WhatsApp to find addresses.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Contact name to look up"}
                },
                "required": ["name"]
            }
        },
        {
            "name": "save_contact",
            "description": "Save or update a contact's info. Use this when the user provides someone's email or phone.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Contact name"},
                    "email": {"type": "string", "description": "Email address"},
                    "phone": {"type": "string", "description": "Phone number with country code"}
                },
                "required": ["name"]
            }
        },
    ])

    # ── Accounting tools (only if session active — added dynamically) ─
    # These are added at call time by brain.py if an accounting session is active

    return tools


def get_accounting_tools() -> list:
    """Return accounting-specific tools (added when session is active)."""
    return [
        {
            "name": "export_accounting",
            "description": "Export the current accounting/reconciliation session as a file.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["excel", "csv", "pdf"],
                        "description": "Export format"
                    }
                },
                "required": ["format"]
            }
        },
        {
            "name": "get_accounting_status",
            "description": "Show the status of the current accounting/reconciliation session.",
            "input_schema": {"type": "object", "properties": {}}
        },
        {
            "name": "update_transactions",
            "description": "Update category or note for one or more transactions by matching their description text.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "updates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string", "description": "Partial text to match the transaction"},
                                "category": {"type": "string", "description": "Category key to assign"},
                                "note": {"type": "string", "description": "Optional note"}
                            },
                            "required": ["description", "category"]
                        },
                        "description": "List of transaction updates"
                    }
                },
                "required": ["updates"]
            }
        },
        {
            "name": "skip_transaction",
            "description": "Skip the current transaction in the accounting review.",
            "input_schema": {"type": "object", "properties": {}}
        },
    ]


def get_invoice_tools() -> list:
    """Return invoice-specific tools (added when invoice data exists)."""
    return [
        {
            "name": "get_invoice_status",
            "description": "Show details of the most recently scanned invoice.",
            "input_schema": {"type": "object", "properties": {}}
        },
        {
            "name": "list_invoices",
            "description": "List all stored invoices with their totals and categories.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max invoices to show (default 10)"}
                }
            }
        },
        {
            "name": "update_invoice",
            "description": "Update fields on an invoice (category, vendor name, total, date, note, etc).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "integer", "description": "Invoice DB id (from list_invoices or get_invoice_status)"},
                    "updates": {
                        "type": "object",
                        "description": "Fields to update. Keys: category, note, vendor_name, invoice_number, invoice_date, total, subtotal, total_iva",
                    }
                },
                "required": ["invoice_id", "updates"]
            }
        },
        {
            "name": "delete_invoice",
            "description": "Delete an invoice by its ID.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "integer", "description": "Invoice DB id to delete"}
                },
                "required": ["invoice_id"]
            }
        },
        {
            "name": "export_invoices",
            "description": "Export all stored invoices as Excel or CSV file.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["excel", "csv"],
                        "description": "Export format"
                    }
                },
                "required": ["format"]
            }
        },
    ]


# ── Tool Executor ────────────────────────────────────────────────────────────

async def execute_tool(name: str, args: dict, chat_id: int, context=None, update=None) -> dict:
    """Execute a tool and return the result as a dict for the AI."""
    try:
        if name == "get_tasks":
            return await _exec_get_tasks(args)
        elif name == "add_task":
            return await _exec_add_task(args)
        elif name == "complete_tasks":
            return await _exec_complete_tasks(args, chat_id)
        elif name == "delete_tasks":
            return await _exec_delete_tasks(args, chat_id)
        elif name == "undo_last_action":
            return await _exec_undo(chat_id)
        elif name == "edit_task":
            return await _exec_edit_task(args)
        elif name == "send_email":
            return await _exec_send_email(args, chat_id)
        elif name == "check_inbox":
            return await _exec_check_inbox(args)
        elif name == "read_email":
            return await _exec_read_email(args)
        elif name == "reply_to_email":
            return await _exec_reply_email(args)
        elif name == "send_whatsapp":
            return await _exec_send_whatsapp(args)
        elif name == "lookup_contact":
            return await _exec_lookup_contact(args)
        elif name == "save_contact":
            return await _exec_save_contact(args)
        elif name == "export_accounting":
            return await _exec_export_accounting(args, context, update)
        elif name == "get_accounting_status":
            return await _exec_accounting_status(context, update)
        elif name == "update_transactions":
            return await _exec_update_transactions(args, context, update)
        elif name == "skip_transaction":
            return await _exec_skip_transaction(context, update)
        elif name == "get_invoice_status":
            return await _exec_invoice_status(context, update)
        elif name == "list_invoices":
            return await _exec_list_invoices(args)
        elif name == "update_invoice":
            return await _exec_update_invoice(args, context)
        elif name == "delete_invoice":
            return await _exec_delete_invoice(args)
        elif name == "export_invoices":
            return await _exec_export_invoices(args, context, update)
        else:
            return {"error": f"Unknown tool: {name}"}
    except Exception as e:
        logger.error(f"Tool {name} failed: {type(e).__name__}: {e}")
        return {"error": f"Tool failed: {type(e).__name__}: {str(e)[:100]}"}


# ── Tool Implementations ─────────────────────────────────────────────────────

async def _exec_get_tasks(args: dict) -> dict:
    from bot.services.notion import notion_service
    filter_type = args.get("filter", "all")
    kwargs = {}
    if filter_type == "today":
        kwargs["due_today"] = True
    elif filter_type == "business":
        kwargs["category"] = "Business"
    elif filter_type == "personal":
        kwargs["category"] = "Personal"
    elif filter_type == "overdue":
        kwargs["overdue"] = True
    elif filter_type == "week":
        kwargs["due_this_week"] = True

    tasks = notion_service.get_tasks(**kwargs)
    if not tasks:
        return {"tasks": [], "message": "No tasks found."}

    task_list = []
    for t in tasks:
        entry = {
            "number": t["index"],
            "title": t["title"],
            "category": t["category"],
            "priority": t["priority"],
        }
        if t.get("due_date"):
            entry["due_date"] = t["due_date"]
        task_list.append(entry)

    return {"tasks": task_list, "count": len(task_list)}


async def _exec_add_task(args: dict) -> dict:
    from bot.services.notion import notion_service
    due_date = None
    if args.get("due_date"):
        try:
            due_date = datetime.fromisoformat(args["due_date"]).date()
        except (ValueError, TypeError):
            pass

    notion_service.add_task(
        title=args["title"],
        category=args.get("category", "Personal"),
        due_date=due_date,
        priority=args.get("priority", "Medium"),
    )
    return {"success": True, "title": args["title"], "category": args.get("category", "Personal")}


async def _exec_complete_tasks(args: dict, chat_id: int) -> dict:
    from bot.services.notion import notion_service
    task_nums = args.get("task_numbers", [])
    tasks = notion_service.get_tasks()
    completed = []
    not_found = []
    undo_entries = []

    for num in sorted(set(task_nums), reverse=True):
        if 1 <= num <= len(tasks):
            task = tasks[num - 1]
            notion_service.mark_complete(task["id"])
            completed.append(task["title"])
            undo_entries.append({"action": "done", "task_id": task["id"], "title": task["title"]})
        else:
            not_found.append(num)

    if undo_entries:
        _undo_buffer[chat_id] = undo_entries

    result = {"completed": list(reversed(completed))}
    if not_found:
        result["not_found"] = not_found
    return result


async def _exec_delete_tasks(args: dict, chat_id: int) -> dict:
    from bot.services.notion import notion_service
    task_nums = args.get("task_numbers", [])
    tasks = notion_service.get_tasks()
    deleted = []
    not_found = []
    undo_entries = []

    for num in sorted(set(task_nums), reverse=True):
        if 1 <= num <= len(tasks):
            task = tasks[num - 1]
            notion_service.delete_task(task["id"])
            deleted.append(task["title"])
            undo_entries.append({"action": "delete", "task_id": task["id"], "title": task["title"]})
        else:
            not_found.append(num)

    if undo_entries:
        _undo_buffer[chat_id] = undo_entries

    result = {"deleted": list(reversed(deleted))}
    if not_found:
        result["not_found"] = not_found
    return result


async def _exec_undo(chat_id: int) -> dict:
    from bot.services.notion import notion_service
    entries = _undo_buffer.pop(chat_id, None)
    if not entries:
        return {"message": "Nothing to undo."}

    restored = []
    failed = []
    for entry in entries:
        try:
            notion_service.restore_task(entry["task_id"])
            restored.append(entry["title"])
        except Exception as e:
            logger.error(f"Undo failed for {entry['title']}: {e}")
            failed.append(entry["title"])

    result = {"restored": restored}
    if failed:
        result["failed"] = failed
    return result


async def _exec_edit_task(args: dict) -> dict:
    from bot.services.notion import notion_service
    task_num = args["task_number"]
    new_title = args["new_title"]
    tasks = notion_service.get_tasks()

    if task_num < 1 or task_num > len(tasks):
        return {"error": f"Task #{task_num} not found."}

    task = tasks[task_num - 1]
    old_title = task["title"]
    notion_service.update_task_title(task["id"], new_title)
    return {"old_title": old_title, "new_title": new_title}


async def _exec_send_email(args: dict, chat_id: int) -> dict:
    from bot.services.email_service import send_email
    to_raw = args["to"]
    subject = args["subject"]
    body = args["body"]

    recipients = [e.strip() for e in to_raw.replace(";", ",").split(",") if e.strip()]
    sent = []
    failed = []

    for rcpt in recipients:
        success, msg = send_email(rcpt, subject, body)
        if success:
            sent.append(rcpt)
            # Auto-save contact
            try:
                from bot.services.contacts_store import contacts_store
                all_contacts = contacts_store.get_all()
                known = any(c.get("email", "").lower() == rcpt.lower() for c in all_contacts.values())
                if not known:
                    local = rcpt.split("@")[0].replace(".", " ").replace("_", " ").title()
                    contacts_store.add_or_update_contact(name=local, email=rcpt, source="auto_email")
            except Exception:
                pass
        else:
            failed.append(f"{rcpt}: {msg}")

    result = {}
    if sent:
        result["sent_to"] = sent
    if failed:
        result["failed"] = failed
    return result


async def _exec_check_inbox(args: dict) -> dict:
    from bot.services.email_inbox import email_inbox
    from bot.handlers.emails import format_inbox
    limit = args.get("limit", 10)
    messages = email_inbox.get_recent(limit)
    return {"inbox": format_inbox(messages), "count": len(messages)}


async def _exec_read_email(args: dict) -> dict:
    from bot.services.email_inbox import email_inbox
    from bot.handlers.emails import format_full_email
    num = args["email_number"]
    msg = email_inbox.get_message_by_num(num)
    if msg:
        return {"email": format_full_email(msg)}
    return {"error": f"Email #{num} not found. Check inbox first."}


async def _exec_reply_email(args: dict) -> dict:
    from bot.services.email_inbox import email_inbox
    num = args["email_number"]
    body = args["body"]
    success, msg = email_inbox.reply_by_num(num, body)
    if success:
        return {"success": True, "message": f"Reply sent to email #{num}"}
    return {"error": f"Failed to reply: {msg}"}


async def _exec_send_whatsapp(args: dict) -> dict:
    from bot.services.whatsapp_service import send_whatsapp
    success, msg = send_whatsapp(args["to_number"], args["message"])
    if success:
        return {"success": True, "sent_to": args["to_number"]}
    return {"error": f"Failed: {msg}"}


async def _exec_lookup_contact(args: dict) -> dict:
    from bot.services.contacts_store import contacts_store
    contact = contacts_store.get_by_name(args["name"])
    if contact:
        return {"found": True, "name": contact.get("name", args["name"]),
                "email": contact.get("email", ""), "phone": contact.get("phone", "")}
    return {"found": False, "message": f"No contact named '{args['name']}' found."}


async def _exec_save_contact(args: dict) -> dict:
    from bot.services.contacts_store import contacts_store
    success = contacts_store.add_or_update_contact(
        name=args["name"], email=args.get("email", ""),
        phone=args.get("phone", ""), source="manual"
    )
    if success:
        return {"success": True, "name": args["name"]}
    return {"error": f"Failed to save contact '{args['name']}'"}


async def _exec_export_accounting(args: dict, context, update) -> dict:
    from bot.handlers.accounting import handle_accounting_export
    fmt = args.get("format", "excel")
    if fmt not in ("excel", "csv", "pdf"):
        fmt = "excel"
    await handle_accounting_export(update, context, fmt)
    return {"success": True, "format": fmt}


async def _exec_accounting_status(context, update) -> dict:
    from bot.handlers.accounting import handle_accounting_status
    await handle_accounting_status(update, context)
    return {"success": True}


async def _exec_update_transactions(args: dict, context, update) -> dict:
    from bot.handlers.accounting import handle_accounting_update
    updates = args.get("updates", [])
    if not updates:
        return {"error": "No transaction updates provided."}
    results = await handle_accounting_update(update, context, updates)
    if results:
        return {"updated": results}
    return {"error": "No matching transactions found."}


async def _exec_skip_transaction(context, update) -> dict:
    from bot.handlers.accounting import _send_next_review
    session = context.user_data.get("acct_session")
    if session:
        session["current_index"] = session.get("current_index", 0) + 1
        await _send_next_review(update, context)
        return {"success": True}
    return {"error": "No active accounting session."}


# ── Invoice Tool Implementations ─────────────────────────────────────────────


async def _exec_invoice_status(context, update) -> dict:
    session = context.user_data.get("invoice_session")
    if not session:
        return {"error": "No active invoice session. Upload a PDF or photo first."}

    invoice = session["invoice"]
    result = {
        "id": invoice.id,
        "vendor": invoice.vendor_name,
        "nif": invoice.vendor_nif,
        "number": invoice.invoice_number,
        "date": invoice.invoice_date,
        "subtotal": invoice.subtotal,
        "total_iva": invoice.total_iva,
        "total": invoice.total,
        "category": invoice.category or "uncategorized",
        "note": invoice.note or "",
        "confidence": invoice.confidence,
        "line_items_count": len(invoice.line_items),
    }
    if invoice.iva_breakdown:
        result["iva_breakdown"] = [
            {"rate": b.rate, "base": b.base_amount, "iva": b.iva_amount}
            for b in invoice.iva_breakdown
        ]
    if invoice.line_items:
        result["line_items"] = [
            {"description": item.description, "qty": item.quantity,
             "unit_price": item.unit_price, "iva_rate": item.iva_rate, "total": item.total}
            for item in invoice.line_items
        ]
    return result


async def _exec_list_invoices(args: dict) -> dict:
    from bot.accounting import storage as acct_db
    limit = args.get("limit", 10)
    invoices = acct_db.load_recent_invoices(limit)
    if not invoices:
        return {"invoices": [], "message": "No invoices stored yet."}
    return {
        "invoices": [
            {"id": inv.id, "vendor": inv.vendor_name, "number": inv.invoice_number,
             "date": inv.invoice_date, "total": inv.total,
             "category": inv.category or "uncategorized"}
            for inv in invoices
        ],
        "count": len(invoices),
    }


async def _exec_update_invoice(args: dict, context) -> dict:
    from bot.accounting import storage as acct_db
    invoice_id = args["invoice_id"]
    updates = args.get("updates", {})

    allowed = {"category", "note", "vendor_name", "vendor_nif", "invoice_number",
               "invoice_date", "total", "subtotal", "total_iva"}
    applied = {}
    for field, value in updates.items():
        if field in allowed:
            acct_db.update_invoice_field(invoice_id, field, value)
            applied[field] = value
            # If updating category, also set confidence to 'user'
            if field == "category":
                acct_db.update_invoice_field(invoice_id, "confidence", "user")

    # Update in-memory session if it's the current invoice
    session = context.user_data.get("invoice_session")
    if session and session["invoice"].id == invoice_id:
        for field, value in applied.items():
            setattr(session["invoice"], field, value)
        if "category" in applied:
            session["invoice"].confidence = "user"

    return {"updated": applied} if applied else {"error": "No valid fields to update."}


async def _exec_delete_invoice(args: dict) -> dict:
    from bot.accounting import storage as acct_db
    invoice_id = args["invoice_id"]
    acct_db.delete_invoice(invoice_id)
    return {"success": True, "deleted_id": invoice_id}


async def _exec_export_invoices(args: dict, context, update) -> dict:
    from bot.accounting.invoice_export import export_invoices_excel, export_invoices_csv
    from bot.accounting import storage as acct_db

    fmt = args.get("format", "excel")
    invoices = acct_db.load_recent_invoices(100)
    if not invoices:
        return {"error": "No invoices to export."}

    # Load full invoice data with line items for export
    full_invoices = []
    for inv in invoices:
        full = acct_db.load_invoice(inv.id)
        if full:
            full_invoices.append(full)

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            if fmt == "csv":
                out = Path(tmp_dir) / "faturas_export.csv"
                export_invoices_csv(full_invoices, out)
            else:
                out = Path(tmp_dir) / "faturas_export.xlsx"
                export_invoices_excel(full_invoices, out)

            with open(out, "rb") as f:
                await update.effective_chat.send_document(
                    document=f, filename=out.name,
                    caption=f"Export de {len(full_invoices)} faturas",
                )
        return {"success": True, "format": fmt, "count": len(full_invoices)}
    except Exception as e:
        logger.error(f"Invoice export error: {e}", exc_info=True)
        return {"error": f"Export failed: {str(e)[:100]}"}
