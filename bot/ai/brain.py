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

    def _get_system_prompt(self, tasks):
        """Build system prompt with personality and context."""
        task_list = self._build_task_context(tasks)
        time_ctx = self._get_time_context()
        stats = self._analyze_tasks(tasks)

        # Build situation awareness
        situation = []
        if stats["overdue"] > 0:
            situation.append(f"{stats['overdue']} overdue task(s)")
        if stats["today"] > 0:
            situation.append(f"{stats['today']} due today")
        if stats["high_priority"] > 0:
            situation.append(f"{stats['high_priority']} high priority")

        situation_str = ", ".join(situation) if situation else "all clear"

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

THEIR TASKS:
{task_list}

RESPOND WITH JSON:
{{"action": "TYPE", "data": {{}}, "response": "your message"}}

ACTIONS:
- "add_task": data: {{"title": "...", "category": "Personal/Business", "priority": "Low/Medium/High", "due_date": "YYYY-MM-DD or null"}}
- "done": data: {{"task_num": N}}
- "delete": data: {{"task_num": N}}
- "list": data: {{"filter": "all/today/business/personal"}}
- "answer": Just chat - use this most of the time

SMART BEHAVIORS:
- "tomorrow", "next week", "friday" -> convert to actual dates
- Guess category from context (work stuff = Business, life stuff = Personal)
- Suggest priority based on urgency/importance
- If they seem stressed, be extra supportive
- If task is vague, maybe ask what specifically they need to do
- Celebrate wins when they complete stuff!

Keep it real. No corporate speak. Just be helpful."""

    async def process(self, user_input, tasks=None):
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

            system_prompt = self._get_system_prompt(tasks or [])

            response_text, error = call_anthropic_chat(
                system_prompt,
                self.conversation_history,
                max_tokens=250  # Shorter responses
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
            try:
                text = response_text.strip()
                start = text.find("{")
                end = text.rfind("}") + 1

                if start >= 0 and end > start:
                    json_str = text[start:end]
                    result = json.loads(json_str)

                    return {
                        "action": result.get("action", "answer"),
                        "data": result.get("data", {}),
                        "response": result.get("response", "")
                    }
                else:
                    return {
                        "action": "answer",
                        "data": {},
                        "response": to_ascii(response_text)[:400]
                    }

            except json.JSONDecodeError:
                return {
                    "action": "answer",
                    "data": {},
                    "response": to_ascii(response_text)[:400]
                }

        except Exception as e:
            return {
                "action": "answer",
                "data": {},
                "response": f"Oops, something went wrong on my end"
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
