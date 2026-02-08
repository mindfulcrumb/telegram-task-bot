"""AI Brain - Claude-powered agent with native tool use."""
import json
import logging
from datetime import datetime, date

import config

logger = logging.getLogger(__name__)


def to_ascii(text):
    """Convert text to ASCII safely."""
    if not text:
        return ""
    try:
        return "".join(c for c in str(text) if ord(c) < 128)
    except Exception:
        return ""


def _call_api(system_prompt, messages, tools=None, max_tokens=2048):
    """Call Anthropic API with tool support."""
    import anthropic

    api_key = config.ANTHROPIC_API_KEY
    if not api_key:
        return None, "No API key configured"

    try:
        client = anthropic.Anthropic(api_key=api_key)

        kwargs = {
            "model": getattr(config, "CLAUDE_MODEL", "claude-sonnet-4-5-20250929"),
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": messages,
            "timeout": 60.0,
        }
        if tools:
            kwargs["tools"] = tools

        return client.messages.create(**kwargs), None

    except anthropic.AuthenticationError:
        return None, "Invalid API key"
    except anthropic.RateLimitError:
        return None, "Rate limit exceeded"
    except anthropic.APIError as e:
        return None, f"API error: {to_ascii(str(e))[:80]}"
    except Exception as e:
        return None, f"Error: {to_ascii(type(e).__name__)}"


# Keep legacy call for weekly_summary and other simple prompts
def call_anthropic_chat(system_prompt, messages, max_tokens=500):
    """Simple API call returning text only (for summaries, analysis)."""
    response, error = _call_api(system_prompt, messages, max_tokens=max_tokens)
    if error:
        return None, error
    if response and response.content:
        text = "".join(b.text for b in response.content if hasattr(b, "text"))
        return text or None, None
    return None, "No response"


class AIBrain:
    """AI Brain with agent loop and native tool use."""

    def _get_time_context(self):
        """Get current time context."""
        now = datetime.now()
        hour = now.hour

        if hour < 12:
            time_of_day = "morning"
        elif hour < 17:
            time_of_day = "afternoon"
        elif hour < 21:
            time_of_day = "evening"
        else:
            time_of_day = "night"

        return {
            "time_of_day": time_of_day,
            "today": now.strftime("%A, %B %d"),
            "date_iso": now.strftime("%Y-%m-%d"),
        }

    def _analyze_tasks(self, tasks):
        """Analyze tasks for context."""
        if not tasks:
            return {"total": 0, "overdue": 0, "today": 0, "high_priority": 0}

        today = date.today()
        today_str = today.isoformat()
        overdue = 0
        due_today = 0
        high_priority = 0

        for t in tasks:
            if t.get("priority") == "High":
                high_priority += 1
            due = t.get("due_date_iso") or t.get("due_date")
            if due:
                try:
                    if isinstance(due, str) and len(due) >= 10 and due[4] == "-":
                        due_str = due[:10]
                        if due_str < today_str:
                            overdue += 1
                        elif due_str == today_str:
                            due_today += 1
                        continue
                    due_date = datetime.fromisoformat(due.replace("Z", "")).date() if isinstance(due, str) else due
                    if due_date < today:
                        overdue += 1
                    elif due_date == today:
                        due_today += 1
                except Exception:
                    pass

        return {"total": len(tasks), "overdue": overdue, "today": due_today, "high_priority": high_priority}

    def _build_task_context(self, tasks):
        """Build task list for context."""
        if not tasks:
            return "No tasks right now."

        lines = []
        today = date.today()
        for i, t in enumerate(tasks, 1):
            title = t.get("title", "Task")
            cat = t.get("category", "Personal")
            pri = t.get("priority", "Medium")
            due = t.get("due_date", "")
            due_str = ""
            if due:
                try:
                    due_date = datetime.fromisoformat(due.replace("Z", "")).date() if isinstance(due, str) else due
                    if due_date < today:
                        due_str = f" - OVERDUE by {(today - due_date).days}d!"
                    elif due_date == today:
                        due_str = " - due TODAY"
                    elif (due_date - today).days == 1:
                        due_str = " - due tomorrow"
                    elif (due_date - today).days <= 7:
                        due_str = f" - due {due_date.strftime('%A')}"
                    else:
                        due_str = f" - due {due_date.strftime('%b %d')}"
                except Exception:
                    due_str = f" - due {due}"
            pri_marker = "!" if pri == "High" else ""
            lines.append(f"{i}. {pri_marker}{title} [{cat}]{due_str}")
        return "\n".join(lines)

    def _get_system_prompt(self, tasks, acct_context=None):
        """Build system prompt — personality and context only, NO action definitions."""
        time_ctx = self._get_time_context()
        stats = self._analyze_tasks(tasks)
        task_list = self._build_task_context(tasks)

        from bot.services.contacts_store import contacts_store
        contacts = contacts_store.format_for_prompt()

        situation = []
        if stats["overdue"] > 0:
            situation.append(f"{stats['overdue']} overdue")
        if stats["today"] > 0:
            situation.append(f"{stats['today']} due today")
        if stats["high_priority"] > 0:
            situation.append(f"{stats['high_priority']} high priority")
        situation_str = ", ".join(situation) if situation else "all clear"

        acct_section = ""
        if acct_context:
            acct_section = f"""

ACCOUNTING/INVOICE DATA ACTIVE:
{acct_context}
The user has accounting data loaded. If their message relates to accounting, invoices, categories, transactions, or exports, use the appropriate tools.
For reconciliation: use export_accounting, get_accounting_status, update_transactions, skip_transaction.
For invoices: use get_invoice_status, list_invoices, update_invoice, delete_invoice, export_invoices.
Valid category keys: fornecedor, transportes, alimentacao, software, combustivel, viagens, material_escritorio, marketing, servicos_profissionais, saude, aluguer, suprimentos, transferencia, receita_vendas, estacionamento, telecomunicacoes, seguros, impostos, formacao, entretenimento, limpeza, manutencao, honorarios, outros
"""

        return f"""You're a chill, helpful assistant managing tasks via Telegram. Talk like a supportive friend, not a robot.

VIBE:
- Be conversational and natural - like texting a friend
- Keep responses SHORT (1-3 sentences max for simple stuff)
- Use casual language, contractions, occasional emoji if it fits
- When asked "what should I focus on" - pick 1-2 things and explain briefly WHY
- Be encouraging but not cheesy
- Celebrate wins when they complete stuff!

RIGHT NOW:
- It's {time_ctx['time_of_day']} on {time_ctx['today']}
- Today's date: {time_ctx['date_iso']}
- Status: {situation_str}

TASKS:
{task_list}

SAVED CONTACTS:
{contacts}
{acct_section}
TOOL USE GUIDELINES:
- Use lookup_contact BEFORE sending emails/WhatsApp to find the right address
- When sending an email, draft the full body yourself based on what the user wants to say
- "tomorrow", "next week", "friday" -> convert to YYYY-MM-DD dates
- Infer category (Personal/Business) and priority from context
- When user says "undo", "bring it back", "that was a mistake" -> use undo_last_action
- After sending an email to someone new, use save_contact to remember them
- You can chain multiple tools in one turn (e.g., look up contact then send email)
- GitHub: you can create issues on any of the user's repos and list available repos
- When creating issues, write a clear title and detailed body with context
- Known repos: telegram-task-bot, terracota-closing_data, protocol, mindfulcrumb-store

Keep it real. No corporate speak. Just be helpful."""

    async def process(self, user_input, chat_id, tasks=None, context=None, update=None, acct_context=None):
        """Agent loop: call Claude with tools, execute tools, repeat until text response."""
        from bot.ai.tools import get_tool_definitions, get_accounting_tools, get_invoice_tools, execute_tool
        from bot.ai import memory

        if not config.ANTHROPIC_API_KEY:
            return None  # Signal fallback to rule-based

        try:
            # Load conversation history + append new user message
            messages = memory.get_history(chat_id)
            messages.append({"role": "user", "content": user_input})

            system_prompt = self._get_system_prompt(tasks or [], acct_context=acct_context)

            # Build tool list (add accounting/invoice tools if session active)
            tools = get_tool_definitions()
            if acct_context:
                tools.extend(get_accounting_tools())
                tools.extend(get_invoice_tools())

            max_turns = getattr(config, "AGENT_MAX_TURNS", 5)
            response = None

            for turn in range(max_turns):
                response, error = _call_api(system_prompt, messages, tools=tools)

                if error:
                    logger.error(f"Agent API error on turn {turn}: {error}")
                    # Save what we have and return error message
                    memory.save_turn(chat_id, "user", user_input)
                    return f"Hmm, hit a snag: {error}"

                if not response or not response.content:
                    memory.save_turn(chat_id, "user", user_input)
                    return None

                # Serialize the assistant's response for message history
                assistant_content = []
                for block in response.content:
                    if hasattr(block, "text"):
                        assistant_content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })

                messages.append({"role": "assistant", "content": assistant_content})

                # Check for tool calls
                tool_calls = [b for b in response.content if b.type == "tool_use"]
                if not tool_calls:
                    break  # Agent is done — has a text response

                # Execute each tool call
                tool_results = []
                for call in tool_calls:
                    logger.info(f"Agent tool call: {call.name}({json.dumps(call.input)[:200]})")
                    result = await execute_tool(
                        call.name, call.input, chat_id,
                        context=context, update=update
                    )
                    logger.info(f"Agent tool result: {call.name} -> {json.dumps(result)[:200]}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": json.dumps(result),
                    })

                messages.append({"role": "user", "content": tool_results})

            # Extract final text from response
            text_parts = []
            if response and response.content:
                for block in response.content:
                    if hasattr(block, "text") and block.text:
                        text_parts.append(block.text)

            final_text = "\n".join(text_parts) if text_parts else None

            # Save conversation to persistent memory
            memory.save_turn(chat_id, "user", user_input)
            if final_text:
                memory.save_turn(chat_id, "assistant", final_text)

            return final_text

        except Exception as e:
            logger.error(f"Agent loop failed: {type(e).__name__}: {e}")
            return "Something went wrong processing that. Try again or use a /command."

    async def weekly_summary(self, tasks):
        """Generate brief, actionable task insights."""
        if not tasks:
            return "No tasks to look at - you're all clear!"

        try:
            stats = self._analyze_tasks(tasks)
            task_list = self._build_task_context(tasks)
            time_ctx = self._get_time_context()

            prompt = f"""It's {time_ctx['time_of_day']} on {time_ctx['today']}.

Here are the tasks:
{task_list}

Give a quick, friendly analysis:
1. What's the vibe? (overwhelmed, manageable, light?)
2. Top 1-2 things to focus on and why
3. One quick tip

Keep it conversational and SHORT - like you're texting a friend. No bullet points or headers, just natural sentences."""

            result, error = call_anthropic_chat("", [{"role": "user", "content": prompt}], max_tokens=200)
            return result if result else (error or "Couldn't analyze right now")

        except Exception:
            return "Had trouble analyzing - try again in a sec"


# Singleton instance
ai_brain = AIBrain()
