"""AI Brain - Claude-powered intelligence for the task bot."""
import json
import urllib.request
import urllib.error
import ssl
from datetime import date
import config


def make_ascii(text) -> str:
    """Convert ANY text to pure ASCII."""
    if text is None:
        return ""
    result = ""
    for char in str(text):
        if ord(char) < 128:
            result += char
        else:
            result += "?"
    return result


class AIBrain:
    """Claude-powered brain for intelligent task management."""

    def __init__(self):
        self.conversation_history = []
        self.api_url = "https://api.anthropic.com/v1/messages"

    def _make_request(self, messages: list, system: str = None, max_tokens: int = 500) -> str:
        """Make direct HTTP request to Anthropic API using urllib (no third-party libs)."""
        try:
            api_key = getattr(config, 'ANTHROPIC_API_KEY', None)
            if not api_key:
                return None

            body = {
                "model": "claude-sonnet-4-20250514",
                "max_tokens": max_tokens,
                "messages": messages,
            }
            if system:
                body["system"] = system

            # Serialize to JSON with ensure_ascii=True - GUARANTEES pure ASCII output
            json_body = json.dumps(body, ensure_ascii=True)
            data_bytes = json_body.encode('ascii')  # Safe because ensure_ascii=True

            # Build request manually
            req = urllib.request.Request(
                self.api_url,
                data=data_bytes,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                method="POST"
            )

            # Make request with timeout
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=60, context=ctx) as response:
                response_bytes = response.read()
                response_text = response_bytes.decode('utf-8')
                data = json.loads(response_text)
                return data["content"][0]["text"]

        except urllib.error.HTTPError as e:
            return "API error: " + str(e.code)
        except urllib.error.URLError as e:
            return "Network error"
        except Exception:
            return "Request failed"

    async def process(self, user_input: str, tasks: list = None) -> dict:
        """Process user input with Claude."""
        safe_input = make_ascii(user_input)

        tasks_context = ""
        if tasks:
            tasks_context = "\n\nCurrent tasks:\n"
            for t in tasks[:10]:
                title = make_ascii(t.get('title', 'Untitled'))
                category = make_ascii(t.get('category', 'Personal'))
                priority = make_ascii(t.get('priority', 'Medium'))
                idx = str(t.get('index', 0))
                tasks_context += "- #" + idx + ": " + title + " (" + category + ", " + priority + ")"
                due = t.get('due_date')
                if due:
                    tasks_context += " due " + str(due)
                tasks_context += "\n"

        today_str = date.today().strftime('%A, %B %d, %Y')
        system_prompt = "You are an intelligent task manager. Today is " + today_str + ".\n\n"
        system_prompt += "Understand user intent and return JSON with an action.\n\n"
        system_prompt += "ACTIONS:\n"
        system_prompt += "- add_task: Create task. data: {title, category, priority, due_date, reminder_minutes}\n"
        system_prompt += "- list: Show tasks. data: {filter: all|today|week|overdue|business|personal}\n"
        system_prompt += "- done: Complete task. data: {task_num}\n"
        system_prompt += "- delete: Remove task. data: {task_num}\n"
        system_prompt += "- answer: Conversational response. data: {text}\n"
        system_prompt += "- summary: Analyze tasks. data: {}\n\n"
        system_prompt += 'Return ONLY valid JSON: {"action": "...", "data": {...}, "response": "..."}\n'
        system_prompt += tasks_context

        messages = self.conversation_history[-6:] + [{"role": "user", "content": safe_input}]

        response_text = self._make_request(messages, system=system_prompt, max_tokens=1024)

        if not response_text or response_text.startswith("API error") or response_text.startswith("Request failed") or response_text.startswith("Network error"):
            return {"action": "fallback", "data": {}, "response": None}

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

    async def weekly_summary(self, tasks: list) -> str:
        """Generate weekly task analysis."""
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
                line += " due " + str(due)
            task_lines.append(line)

        tasks_text = "\n".join(task_lines)

        prompt = "Analyze these tasks briefly:\n\n"
        prompt += tasks_text + "\n\n"
        prompt += "Give:\n1. One-line overview\n2. Top 3 priorities\n3. Any concerns\n4. One tip\n\nBe concise."

        messages = [{"role": "user", "content": prompt}]
        result = self._make_request(messages, max_tokens=500)

        if result:
            return make_ascii(result)  # Ensure response is also ASCII
        return "Analysis unavailable"


ai_brain = AIBrain()
