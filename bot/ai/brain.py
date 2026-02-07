"""AI Brain - Claude-powered intelligence for the task bot."""
import json
from datetime import datetime, date
import config


def to_ascii(text):
    """Convert text to ASCII safely."""
    if not text:
        return ""
    try:
        return "".join(c for c in str(text) if ord(c) < 128)
    except Exception:
        return ""


def call_anthropic_chat(system_prompt, messages, max_tokens=500):
    """Call Anthropic API with conversation history."""
    import anthropic

    api_key = config.ANTHROPIC_API_KEY
    if not api_key:
        return None, "No API key configured"

    try:
        client = anthropic.Anthropic(api_key=api_key)

        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages
        )

        if response.content:
            return response.content[0].text, None
        return None, "No response"

    except anthropic.AuthenticationError:
        return None, "Invalid API key"
    except anthropic.RateLimitError:
        return None, "Rate limit exceeded"
    except anthropic.APIError as e:
        return None, f"API error: {to_ascii(str(e))[:50]}"
    except Exception as e:
        return None, f"Error: {to_ascii(type(e).__name__)}"


def call_anthropic(prompt_text):
    """Simple single-turn API call (legacy)."""
    result, error = call_anthropic_chat("", [{"role": "user", "content": prompt_text}])
    return result if result else (error or "No response")


class AIBrain:
    """AI Brain for conversational task management."""

    def __init__(self):
        self.conversation_history = []
        self.max_history = 10

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
            "date_iso": now.strftime("%Y-%m-%d")
        }

    def _analyze_tasks(self, tasks):
        """Analyze tasks for context."""
        if not tasks:
            return {"total": 0, "overdue": 0, "today": 0, "high_priority": 0}

        today = date.today()
        overdue = 0
        due_today = 0
        high_priority = 0

        for t in tasks:
            if t.get("priority") == "High":
                high_priority += 1

            due = t.get("due_date")
            if due:
                try:
                    if isinstance(due, str):
                        due_date = datetime.fromisoformat(due.replace("Z", "")).date()
                    else:
                        due_date = due

                    if due_date < today:
                        overdue += 1
                    elif due_date == today:
                        due_today += 1
                except:
                    pass

        return {
            "total": len(tasks),
            "overdue": overdue,
            "today": due_today,
            "high_priority": high_priority
        }

    def _build_task_context(self, tasks):
        """Build task list for context."""
        if not tasks:
            return "You have no tasks right now - fresh slate!"

        lines = []
        today = date.today()

        for i, t in enumerate(tasks, 1):
            title = to_ascii(t.get("title", "Task")) or "Task"
            cat = t.get("category", "Personal")
            pri = t.get("priority", "Medium")
            due = t.get("due_date", "")

            # Format due date naturally
            due_str = ""
            if due:
                try:
                    if isinstance(due, str):
                        due_date = datetime.fromisoformat(due.replace("Z", "")).date()
                    else:
                        due_date = due

                    if due_date < today:
                        days_overdue = (today - due_date).days
                        due_str = f" - OVERDUE by {days_overdue}d!"
                    elif due_date == today:
                        due_str = " - due TODAY"
                    elif (due_date - today).days == 1:
                        due_str = " - due tomorrow"
                    elif (due_date - today).days <= 7:
                        due_str = f" - due {due_date.strftime('%A')}"
                    else:
                        due_str = f" - due {due_date.strftime('%b %d')}"
                except:
                    due_str = f" - due {due}"

            pri_marker = "!" if pri == "High" else ""
            lines.append(f"{i}. {pri_marker}{title} [{cat}]{due_str}")

        return "\n".join(lines)

    def _get_contacts_context(self):
        """Get contacts for context."""
        from bot.services.contacts_store import contacts_store
        return contacts_store.format_for_prompt()

    def _get_capabilities(self):
        """Check what capabilities are available."""
        from bot.services.email_service import is_email_configured
        from bot.services.whatsapp_service import is_whatsapp_configured

        caps = []
        if is_email_configured():
            caps.append("email")
        if is_whatsapp_configured():
            caps.append("whatsapp")
        return caps

    def _get_system_prompt(self, tasks, acct_context=None):
        """Build system prompt with personality and context."""
        task_list = self._build_task_context(tasks)
        time_ctx = self._get_time_context()
        stats = self._analyze_tasks(tasks)
        contacts = self._get_contacts_context()
        capabilities = self._get_capabilities()

        # Build situation awareness
        situation = []
        if stats["overdue"] > 0:
            situation.append(f"{stats['overdue']} overdue task(s)")
        if stats["today"] > 0:
            situation.append(f"{stats['today']} due today")
        if stats["high_priority"] > 0:
            situation.append(f"{stats['high_priority']} high priority")

        situation_str = ", ".join(situation) if situation else "all clear"

        # Build capabilities section
        caps_text = ""
        if "email" in capabilities:
            caps_text += '\n- "preview_email": data: {"to": "email@example.com", "subject": "...", "body": "..."} - ALWAYS use this first to show draft'
            caps_text += '\n- "confirm_email": data: {} - Use when user confirms the previewed email (says yes, send it, looks good, etc.)'
            caps_text += '\n- "check_inbox": data: {} - Show recent received emails'
            caps_text += '\n- "read_email": data: {"email_num": N} - Read full content of email #N from inbox'
            caps_text += '\n- "reply_email": data: {"email_num": N, "body": "reply text"} - Reply to email #N'
        if "whatsapp" in capabilities:
            caps_text += '\n- "send_whatsapp": data: {"to": "+1234567890", "message": "..."}'
        if capabilities:
            caps_text += '\n- "save_contact": data: {"name": "Will", "email": "will@example.com", "phone": "+1234567890"}'

        caps_note = ""
        if capabilities:
            caps_note = (
                f"\n\nYOU CAN ALSO:"
                f"\n- Send emails and WhatsApp messages when asked"
                f"\n- Use saved contacts by name (e.g., 'email will' -> looks up will's email)"
                f"\n- Save new contacts when you learn someone's email or phone"
                f"\n- IMPORTANT: When sending an email to someone NEW (not in contacts), also use save_contact to remember them for next time"
                f"\n\nSAVED CONTACTS:\n{contacts}"
            )

        # Build accounting context section
        acct_section = ""
        acct_actions = ""
        if acct_context:
            acct_section = f"""

ACCOUNTING SESSION:
{acct_context}
The user recently uploaded a bank reconciliation PDF. Messages may relate to this accounting session.
"""
            acct_actions = """
- "accounting_export": data: {"format": "excel/csv/pdf"} - Export the current reconciliation session
- "accounting_status": data: {} - Show status of the current accounting session
- "accounting_skip": data: {} - Skip the current transaction in review"""

        # Build clarification note when accounting is active
        acct_behavior = ""
        if acct_context:
            acct_behavior = """
- IMPORTANT: There is an active accounting/reconciliation session. If the user's message seems related to accounting (export, status, categories, transactions, PDF, reconciliation, skip), use the accounting actions.
- If the user says "export", "send me the file", "gerar ficheiro", "exportar" -> use "accounting_export" with the format they want (default to "excel" if not specified)
- If the user says "status", "how many left", "quantas faltam" -> use "accounting_status"
- If the user says "skip", "next", "saltar" -> use "accounting_skip"
- If the user's message is clearly a task (e.g., "buy groceries tomorrow") -> still add it as a task
- If you're NOT SURE if the message is about accounting or something else, use "answer" action and ASK the user: "Are you referring to the reconciliation session, or do you want me to do something else (create a task, send an email, etc.)?"
"""

        return f"""You're a chill, helpful assistant managing tasks via Telegram. Talk like a supportive friend, not a robot.

VIBE:
- Be conversational and natural - like texting a friend
- Keep responses SHORT (1-3 sentences max for simple stuff)
- Don't dump walls of text - break it up naturally
- Use casual language, contractions, occasional emoji if it fits
- When asked "what should I focus on" - just pick 1-2 things and explain briefly WHY, don't list everything
- Be encouraging but not cheesy
- Ask follow-up questions when it makes sense

RIGHT NOW:
- It's {time_ctx['time_of_day']} on {time_ctx['today']}
- Today's date for due dates: {time_ctx['date_iso']}
- Status: {situation_str}
{acct_section}
THEIR TASKS:
{task_list}{caps_note}

CRITICAL: You MUST respond with ONLY a JSON object - no text before or after it. No preamble, no explanation, just the JSON:
{{"action": "TYPE", "data": {{}}, "response": "your message"}}
For emails, write a proper email body in the "body" field based on the user's context. Put your friendly chat message in "response".

ACTIONS:
- "add_task": data: {{"title": "...", "category": "Personal/Business", "priority": "Low/Medium/High", "due_date": "YYYY-MM-DD or null"}}
- "done": data: {{"task_num": N}}
- "delete": data: {{"task_num": N}}
- "list": data: {{"filter": "all/today/business/personal"}}{caps_text}{acct_actions}
- "answer": Just chat - use this most of the time

SMART BEHAVIORS:
- "tomorrow", "next week", "friday" -> convert to actual dates
- Guess category from context (work stuff = Business, life stuff = Personal)
- Suggest priority based on urgency/importance
- If they seem stressed, be extra supportive
- If task is vague, maybe ask what specifically they need to do
- Celebrate wins when they complete stuff!
- For emails/messages: if no recipient specified, ASK who to send to
- When asked to send an email, DRAFT the full email content yourself based on context. Use "preview_email" action with a complete subject and body you wrote. Be creative and natural with the email text.
- The user will then review your draft. If they say to change something ("make it shorter", "change the subject", "add a greeting"), generate a NEW "preview_email" with the updated content.
- When user confirms the email (says yes, send it, looks good, go ahead, etc.), use "confirm_email" action with empty data - the system remembers the draft.
- For MULTIPLE recipients, put all emails comma-separated in the "to" field: "to": "will@x.com, john@x.com"
- When user says "email Will" and Will is in contacts, use their saved email
- When user provides a new contact detail (like "Will's email is will@x.com"), save it with save_contact
- When user says "check my email", "any new emails?", "inbox" -> use "check_inbox"
- When user says "read email 2", "open email 1", "what does email 3 say" -> use "read_email" with the number
- When user says "reply to email 1: sounds good" -> use "reply_email" with the number and draft the reply body based on their message
{acct_behavior}
Keep it real. No corporate speak. Just be helpful."""

    async def process(self, user_input, tasks=None, acct_context=None):
        """Process user input and return action."""
        if not config.ANTHROPIC_API_KEY:
            return {"action": "fallback", "data": {}, "response": None}

        try:
            self.conversation_history.append({
                "role": "user",
                "content": to_ascii(user_input) or "hello"
            })

            if len(self.conversation_history) > self.max_history * 2:
                self.conversation_history = self.conversation_history[-self.max_history * 2:]

            system_prompt = self._get_system_prompt(tasks or [], acct_context=acct_context)

            response_text, error = call_anthropic_chat(
                system_prompt,
                self.conversation_history,
                max_tokens=1024
            )

            if error:
                return {"action": "answer", "data": {}, "response": f"Hmm, hit a snag: {error}"}

            if not response_text:
                return {"action": "fallback", "data": {}, "response": None}

            self.conversation_history.append({
                "role": "assistant",
                "content": response_text
            })

            # Parse JSON response
            result = self._parse_ai_response(response_text)
            return result

        except Exception as e:
            return {
                "action": "answer",
                "data": {},
                "response": f"Oops, something went wrong on my end"
            }

    def _parse_ai_response(self, response_text):
        """Parse AI response JSON with recovery for truncated/malformed output."""
        import re

        text = response_text.strip()

        # Try 1: Standard JSON extraction
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                json_str = text[start:end]
                result = json.loads(json_str)
                return {
                    "action": result.get("action", "answer"),
                    "data": result.get("data", {}),
                    "response": result.get("response", "")
                }
            except json.JSONDecodeError:
                pass

        # Try 2: Recovery for truncated JSON - extract fields with regex
        action_match = re.search(r'"action"\s*:\s*"([^"]+)"', text)
        if action_match:
            action = action_match.group(1)

            if action in ("send_email", "preview_email"):
                to_match = re.search(r'"to"\s*:\s*"([^"]+)"', text)
                subj_match = re.search(r'"subject"\s*:\s*"([^"]+)"', text)
                body_match = re.search(r'"body"\s*:\s*"((?:[^"\\]|\\.)*)', text)
                resp_match = re.search(r'"response"\s*:\s*"([^"]+)"', text)

                if to_match and subj_match:
                    return {
                        "action": action,
                        "data": {
                            "to": to_match.group(1),
                            "subject": subj_match.group(1),
                            "body": body_match.group(1) if body_match else subj_match.group(1)
                        },
                        "response": resp_match.group(1) if resp_match else ""
                    }

            elif action in ("read_email", "reply_email"):
                num_match = re.search(r'"email_num"\s*:\s*(\d+)', text)
                body_match = re.search(r'"body"\s*:\s*"((?:[^"\\]|\\.)*)', text)
                resp_match = re.search(r'"response"\s*:\s*"([^"]+)"', text)
                data = {}
                if num_match:
                    data["email_num"] = int(num_match.group(1))
                if body_match and action == "reply_email":
                    data["body"] = body_match.group(1)
                return {
                    "action": action,
                    "data": data,
                    "response": resp_match.group(1) if resp_match else ""
                }

            elif action == "save_contact":
                name_match = re.search(r'"name"\s*:\s*"([^"]+)"', text)
                email_match = re.search(r'"email"\s*:\s*"([^"]+)"', text)
                phone_match = re.search(r'"phone"\s*:\s*"([^"]*)"', text)
                resp_match = re.search(r'"response"\s*:\s*"([^"]+)"', text)

                if name_match:
                    return {
                        "action": "save_contact",
                        "data": {
                            "name": name_match.group(1),
                            "email": email_match.group(1) if email_match else "",
                            "phone": phone_match.group(1) if phone_match else ""
                        },
                        "response": resp_match.group(1) if resp_match else ""
                    }

            # Generic recovery for other actions
            data = {}
            resp_match = re.search(r'"response"\s*:\s*"([^"]+)"', text)
            return {
                "action": action,
                "data": data,
                "response": resp_match.group(1) if resp_match else ""
            }

        # Fallback: treat as plain text answer
        return {
            "action": "answer",
            "data": {},
            "response": to_ascii(text)[:400]
        }

    def clear_history(self):
        """Clear conversation history."""
        self.conversation_history = []

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

        except Exception as e:
            return "Had trouble analyzing - try again in a sec"


# Singleton instance
ai_brain = AIBrain()
