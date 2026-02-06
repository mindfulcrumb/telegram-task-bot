"""AI Brain - Claude-powered intelligence for the task bot."""
import json
from datetime import datetime, date, timedelta
from anthropic import Anthropic
import config


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

        # Build context about current tasks (with safe string handling)
        tasks_context = ""
        if tasks:
            tasks_context = "\n\nCurrent tasks:\n"
            for t in tasks[:10]:
                title = str(t.get('title', 'Untitled'))
                category = str(t.get('category', 'Personal'))
                priority = str(t.get('priority', 'Medium'))
                tasks_context += f"- #{t['index']}: {title} ({category}, {priority})"
                if t.get('due_date'):
                    tasks_context += f" due {t['due_date']}"
                tasks_context += "\n"

        system_prompt = f"""You are an intelligent task manager. Today is {date.today().strftime('%A, %B %d, %Y')}.

Understand user intent and return JSON with an action.

ACTIONS:
- add_task: Create task. data: {{title, category, priority, due_date, reminder_minutes}}
- list: Show tasks. data: {{filter: "all"|"today"|"week"|"overdue"|"business"|"personal"}}
- done: Complete task. data: {{task_num}}
- delete: Remove task. data: {{task_num}}
- remind: Set reminder. data: {{task_num, minutes}}
- answer: Conversational response. data: {{text}}
- summary: Analyze tasks. data: {{}}

Return ONLY valid JSON:
{{"action": "...", "data": {{...}}, "response": "what to say"}}

SMART RULES:
- "meeting with client" → Business, Medium priority
- "URGENT" or "ASAP" → High priority
- "groceries", "gym" → Personal
- "next week" → due_date = next Monday
- "remind me in 2 hours" → reminder_minutes: 120
- Questions about tasks → use 'answer' with analysis
- Greetings → friendly 'answer'
{tasks_context}"""

        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                system=system_prompt,
                messages=[
                    *self.conversation_history[-6:],
                    {"role": "user", "content": user_input}
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

            # Update history
            self.conversation_history.append({"role": "user", "content": user_input})
            self.conversation_history.append({"role": "assistant", "content": response_text})
            if len(self.conversation_history) > 20:
                self.conversation_history = self.conversation_history[-10:]

            return result

        except Exception as e:
            # Use repr() to safely log errors with non-ASCII characters
            print(f"[AI Brain] Error: {repr(e)}")
            return {"action": "fallback", "data": {}, "response": None}

    def _safe_str(self, text: str) -> str:
        """Safely handle text that may contain non-ASCII characters."""
        if not text:
            return ""
        # Ensure we're working with a proper string
        if isinstance(text, bytes):
            text = text.decode('utf-8', errors='replace')
        return str(text)

    async def weekly_summary(self, tasks: list) -> str:
        """Generate weekly task analysis."""
        client = self._get_client()
        if not client or not tasks:
            return "No tasks to analyze."

        # Build tasks text with safe string handling
        task_lines = []
        for t in tasks:
            title = self._safe_str(t.get('title', 'Untitled'))
            category = self._safe_str(t.get('category', 'Personal'))
            priority = self._safe_str(t.get('priority', 'Medium'))
            line = f"- {title} ({category}, {priority})"
            if t.get('due_date'):
                line += f" due {t['due_date']}"
            task_lines.append(line)

        tasks_text = "\n".join(task_lines)

        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
                messages=[{
                    "role": "user",
                    "content": f"""Analyze these tasks briefly:

{tasks_text}

Give:
1. One-line overview
2. Top 3 priorities
3. Any concerns
4. One tip

Be concise."""
                }]
            )
            return response.content[0].text
        except Exception as e:
            # Use repr() to safely handle non-ASCII in error messages
            return f"Analysis unavailable: {repr(e)}"


# Singleton
ai_brain = AIBrain()
