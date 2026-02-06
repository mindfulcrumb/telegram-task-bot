"""AI Brain - Claude-powered intelligence for the task bot."""
import json
import os
import logging
from datetime import datetime, date, timedelta

# Suppress httpx/httpcore logging which can cause encoding issues
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("httpcore").setLevel(logging.ERROR)
logging.getLogger("anthropic").setLevel(logging.ERROR)

# Set encoding environment variable
os.environ['PYTHONIOENCODING'] = 'utf-8'

from anthropic import Anthropic
import config


def to_ascii_safe(text) -> str:
    """
    Convert text to ASCII-safe string.
    Replaces all non-ASCII characters to avoid encoding errors.
    """
    if text is None:
        return ""
    try:
        # Convert to string first
        text = str(text)
        # Build ASCII-only string character by character
        result = []
        for char in text:
            if ord(char) < 128:
                result.append(char)
            else:
                result.append('?')
        return ''.join(result)
    except Exception:
        return "unknown"


class AIBrain:
    """Claude-powered brain for intelligent task management."""

    def __init__(self):
        self.client = None  # Lazy init
        self.conversation_history = []

    def _get_client(self):
        """Lazy initialize the Anthropic client."""
        if self.client is None:
            api_key = getattr(config, 'ANTHROPIC_API_KEY', None)
            if api_key:
                self.client = Anthropic(api_key=api_key)
        return self.client

    async def process(self, user_input: str, tasks: list = None) -> dict:
        """
        Process any user input with Claude and return structured action.

        Returns dict with:
        - action: 'add_task', 'list', 'done', 'delete', 'answer', etc.
        - data: action-specific data
        - response: what to tell the user
        """
        client = self._get_client()
        if not client:
            return {"action": "fallback", "data": {}, "response": None}

        # Make user input ASCII-safe
        safe_input = to_ascii_safe(user_input)

        # Build context about current tasks (ASCII-safe)
        tasks_context = ""
        if tasks:
            tasks_context = "\n\nCurrent tasks:\n"
            for t in tasks[:10]:
                title = to_ascii_safe(t.get('title', 'Untitled'))
                category = to_ascii_safe(t.get('category', 'Personal'))
                priority = to_ascii_safe(t.get('priority', 'Medium'))
                tasks_context += "- #" + str(t['index']) + ": " + title + " (" + category + ", " + priority + ")"
                if t.get('due_date'):
                    tasks_context += " due " + str(t['due_date'])
                tasks_context += "\n"

        system_prompt = """You are an intelligent task manager. Today is """ + date.today().strftime('%A, %B %d, %Y') + """.

Understand user intent and return JSON with an action.

ACTIONS:
- add_task: Create task. data: {title, category, priority, due_date, reminder_minutes}
- list: Show tasks. data: {filter: "all"|"today"|"week"|"overdue"|"business"|"personal"}
- done: Complete task. data: {task_num}
- delete: Remove task. data: {task_num}
- remind: Set reminder. data: {task_num, minutes}
- answer: Conversational response. data: {text}
- summary: Analyze tasks. data: {}

Return ONLY valid JSON:
{"action": "...", "data": {...}, "response": "what to say"}

SMART RULES:
- "meeting with client" -> Business, Medium priority
- "URGENT" or "ASAP" -> High priority
- "groceries", "gym" -> Personal
- "next week" -> due_date = next Monday
- "remind me in 2 hours" -> reminder_minutes: 120
- Questions about tasks -> use 'answer' with analysis
- Greetings -> friendly 'answer'
""" + tasks_context

        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                system=system_prompt,
                messages=[
                    *self.conversation_history[-6:],
                    {"role": "user", "content": safe_input}
                ]
            )

            response_text = response.content[0].text

            # Extract JSON
            try:
                json_start = response_text.find('{')
                json_end = response_text.rfind('}') + 1
                if json_start != -1 and json_end > json_start:
                    result = json.loads(response_text[json_start:json_end])
                else:
                    result = json.loads(response_text)
            except json.JSONDecodeError:
                result = {
                    "action": "answer",
                    "data": {"text": response_text},
                    "response": response_text
                }

            # Update history (with safe strings)
            self.conversation_history.append({"role": "user", "content": safe_input})
            self.conversation_history.append({"role": "assistant", "content": to_ascii_safe(response_text)})
            if len(self.conversation_history) > 20:
                self.conversation_history = self.conversation_history[-10:]

            return result

        except UnicodeEncodeError as ue:
            return {"action": "fallback", "data": {}, "response": None}
        except UnicodeDecodeError as ud:
            return {"action": "fallback", "data": {}, "response": None}
        except Exception as e:
            error_msg = to_ascii_safe(str(e))
            return {"action": "fallback", "data": {}, "response": None}

    async def weekly_summary(self, tasks: list) -> str:
        """Generate weekly task analysis."""
        client = self._get_client()
        if not client or not tasks:
            return "No tasks to analyze."

        # Build tasks text (ASCII-safe for API call)
        task_lines = []
        for t in tasks:
            title = to_ascii_safe(t.get('title', 'Untitled'))
            category = to_ascii_safe(t.get('category', 'Personal'))
            priority = to_ascii_safe(t.get('priority', 'Medium'))
            line = "- " + title + " (" + category + ", " + priority + ")"
            if t.get('due_date'):
                line += " due " + str(t['due_date'])
            task_lines.append(line)

        tasks_text = "\n".join(task_lines)

        prompt = """Analyze these tasks briefly:

""" + tasks_text + """

Give:
1. One-line overview
2. Top 3 priorities
3. Any concerns
4. One tip

Be concise."""

        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                messages=[{
                    "role": "user",
                    "content": prompt
                }]
            )
            return response.content[0].text
        except UnicodeEncodeError:
            return "Analysis unavailable: encoding error with task data"
        except UnicodeDecodeError:
            return "Analysis unavailable: encoding error with task data"
        except Exception as e:
            error_msg = to_ascii_safe(str(e))
            return "Analysis unavailable: " + error_msg


# Singleton
ai_brain = AIBrain()
