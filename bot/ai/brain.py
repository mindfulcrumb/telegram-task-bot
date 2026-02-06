"""AI Brain - Claude-powered intelligence for the task bot."""
import json
import os
import sys
import logging
from datetime import datetime, date, timedelta

# Suppress ALL logging from httpx/httpcore/anthropic
for logger_name in ["httpx", "httpcore", "anthropic", "httpcore.connection", "httpcore.http11"]:
    logging.getLogger(logger_name).setLevel(logging.CRITICAL)
    logging.getLogger(logger_name).disabled = True

# Set encoding environment variables
os.environ['PYTHONIOENCODING'] = 'utf-8'
os.environ['PYTHONLEGACYWINDOWSSTDIO'] = 'utf-8'

from anthropic import Anthropic
import config


def make_ascii(text) -> str:
    """Convert ANY text to pure ASCII. No exceptions possible."""
    if text is None:
        return ""
    result = ""
    for char in str(text):
        code = ord(char)
        if code < 128:
            result += char
        else:
            result += "?"
    return result


class AIBrain:
    """Claude-powered brain for intelligent task management."""

    def __init__(self):
        self.client = None
        self.conversation_history = []

    def _get_client(self):
        """Lazy initialize the Anthropic client."""
        if self.client is None:
            api_key = getattr(config, 'ANTHROPIC_API_KEY', None)
            if api_key:
                self.client = Anthropic(api_key=api_key)
        return self.client

    async def process(self, user_input: str, tasks: list = None) -> dict:
        """Process user input with Claude."""
        client = self._get_client()
        if not client:
            return {"action": "fallback", "data": {}, "response": None}

        safe_input = make_ascii(user_input)

        tasks_context = ""
        if tasks:
            tasks_context = "\n\nCurrent tasks:\n"
            for t in tasks[:10]:
                title = make_ascii(t.get('title', 'Untitled'))
                category = make_ascii(t.get('category', 'Personal'))
                priority = make_ascii(t.get('priority', 'Medium'))
                idx = str(t.get('index', 0))
                tasks_context = tasks_context + "- #" + idx + ": " + title + " (" + category + ", " + priority + ")"
                due = t.get('due_date')
                if due:
                    tasks_context = tasks_context + " due " + str(due)
                tasks_context = tasks_context + "\n"

        today_str = date.today().strftime('%A, %B %d, %Y')
        system_prompt = "You are an intelligent task manager. Today is " + today_str + ".\n\n"
        system_prompt = system_prompt + "Understand user intent and return JSON with an action.\n\n"
        system_prompt = system_prompt + "ACTIONS:\n"
        system_prompt = system_prompt + "- add_task: Create task. data: {title, category, priority, due_date, reminder_minutes}\n"
        system_prompt = system_prompt + "- list: Show tasks. data: {filter: all|today|week|overdue|business|personal}\n"
        system_prompt = system_prompt + "- done: Complete task. data: {task_num}\n"
        system_prompt = system_prompt + "- delete: Remove task. data: {task_num}\n"
        system_prompt = system_prompt + "- remind: Set reminder. data: {task_num, minutes}\n"
        system_prompt = system_prompt + "- answer: Conversational response. data: {text}\n"
        system_prompt = system_prompt + "- summary: Analyze tasks. data: {}\n\n"
        system_prompt = system_prompt + "Return ONLY valid JSON: {action: ..., data: {...}, response: what to say}\n\n"
        system_prompt = system_prompt + "SMART RULES:\n"
        system_prompt = system_prompt + "- meeting with client -> Business, Medium priority\n"
        system_prompt = system_prompt + "- URGENT or ASAP -> High priority\n"
        system_prompt = system_prompt + "- groceries, gym -> Personal\n"
        system_prompt = system_prompt + "- next week -> due_date = next Monday\n"
        system_prompt = system_prompt + "- remind me in 2 hours -> reminder_minutes: 120\n"
        system_prompt = system_prompt + "- Questions about tasks -> use answer with analysis\n"
        system_prompt = system_prompt + "- Greetings -> friendly answer\n"
        system_prompt = system_prompt + tasks_context

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

            self.conversation_history.append({"role": "user", "content": safe_input})
            self.conversation_history.append({"role": "assistant", "content": make_ascii(response_text)})
            if len(self.conversation_history) > 20:
                self.conversation_history = self.conversation_history[-10:]

            return result

        except BaseException:
            return {"action": "fallback", "data": {}, "response": None}

    async def weekly_summary(self, tasks: list) -> str:
        """Generate weekly task analysis."""
        client = self._get_client()
        if not client:
            return "AI client not available. Check ANTHROPIC_API_KEY."
        if not tasks:
            return "No tasks to analyze."

        # Build PURE ASCII task list
        task_lines = []
        for t in tasks:
            title = make_ascii(t.get('title', 'Untitled'))
            category = make_ascii(t.get('category', 'Personal'))
            priority = make_ascii(t.get('priority', 'Medium'))
            line = "- " + title + " (" + category + ", " + priority + ")"
            due = t.get('due_date')
            if due:
                line = line + " due " + str(due)
            task_lines.append(line)

        tasks_text = "\n".join(task_lines)

        prompt = "Analyze these tasks briefly:\n\n"
        prompt = prompt + tasks_text + "\n\n"
        prompt = prompt + "Give:\n1. One-line overview\n2. Top 3 priorities\n3. Any concerns\n4. One tip\n\nBe concise."

        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text
        except BaseException as e:
            # Catch absolutely everything
            try:
                err = make_ascii(str(e))
                return "Analysis error: " + err
            except BaseException:
                return "Analysis unavailable due to system error"


ai_brain = AIBrain()
