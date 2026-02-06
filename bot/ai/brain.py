"""AI Brain - Claude-powered intelligence for the task bot."""
import json
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
        self.max_history = 10  # Keep last 10 exchanges

    def _build_task_context(self, tasks):
        """Build task list for context."""
        if not tasks:
            return "No tasks currently."

        lines = []
        for i, t in enumerate(tasks, 1):
            title = to_ascii(t.get("title", "Task")) or "Task"
            cat = t.get("category", "Personal")
            pri = t.get("priority", "Medium")
            due = t.get("due_date", "")
            due_str = f", due {due}" if due else ""
            lines.append(f"{i}. {title} [{cat}, {pri}{due_str}]")

        return "\n".join(lines)

    def _get_system_prompt(self, tasks):
        """Build system prompt with task context."""
        task_list = self._build_task_context(tasks)

        return f"""You are a helpful task management assistant in a Telegram bot.

CURRENT TASKS:
{task_list}

You can help the user with:
- Adding new tasks
- Marking tasks as done
- Deleting tasks
- Listing/filtering tasks
- Answering questions about their tasks
- General conversation and advice

When the user wants to perform an action, respond with JSON in this exact format:
{{"action": "ACTION_TYPE", "data": {{}}, "response": "Your message to the user"}}

ACTION TYPES:
- "add_task": Add a new task. data: {{"title": "task title", "category": "Personal/Business", "priority": "Low/Medium/High", "due_date": "YYYY-MM-DD or null"}}
- "done": Mark task complete. data: {{"task_num": NUMBER}}
- "delete": Delete a task. data: {{"task_num": NUMBER}}
- "list": Show tasks. data: {{"filter": "all/today/business/personal"}}
- "summary": Analyze tasks (triggers /analyze)
- "answer": Just respond to the user. data: {{"text": "your response"}}

IMPORTANT:
- For casual conversation, greetings, or questions, use "answer" action
- Task numbers refer to the numbered list above
- Always include a friendly "response" message
- Keep responses concise (Telegram messages)
- If uncertain about task number, ask for clarification with "answer" action

Respond ONLY with the JSON object, no other text."""

    async def process(self, user_input, tasks=None):
        """Process user input and return action."""
        if not config.ANTHROPIC_API_KEY:
            return {"action": "fallback", "data": {}, "response": None}

        try:
            # Add user message to history
            self.conversation_history.append({
                "role": "user",
                "content": to_ascii(user_input) or "hello"
            })

            # Keep history manageable
            if len(self.conversation_history) > self.max_history * 2:
                self.conversation_history = self.conversation_history[-self.max_history * 2:]

            # Get system prompt with current tasks
            system_prompt = self._get_system_prompt(tasks or [])

            # Call Claude
            response_text, error = call_anthropic_chat(
                system_prompt,
                self.conversation_history,
                max_tokens=300
            )

            if error:
                return {"action": "answer", "data": {}, "response": f"AI error: {error}"}

            if not response_text:
                return {"action": "fallback", "data": {}, "response": None}

            # Add assistant response to history
            self.conversation_history.append({
                "role": "assistant",
                "content": response_text
            })

            # Parse JSON response
            try:
                # Clean up response - find JSON object
                text = response_text.strip()

                # Find JSON boundaries
                start = text.find("{")
                end = text.rfind("}") + 1

                if start >= 0 and end > start:
                    json_str = text[start:end]
                    result = json.loads(json_str)

                    action = result.get("action", "answer")
                    data = result.get("data", {})
                    response = result.get("response", "")

                    return {
                        "action": action,
                        "data": data,
                        "response": response
                    }
                else:
                    # No JSON found, treat as plain answer
                    return {
                        "action": "answer",
                        "data": {},
                        "response": to_ascii(response_text)[:500]
                    }

            except json.JSONDecodeError:
                # Couldn't parse JSON, return as plain answer
                return {
                    "action": "answer",
                    "data": {},
                    "response": to_ascii(response_text)[:500]
                }

        except Exception as e:
            return {
                "action": "answer",
                "data": {},
                "response": f"Sorry, I encountered an error: {to_ascii(type(e).__name__)}"
            }

    def clear_history(self):
        """Clear conversation history."""
        self.conversation_history = []

    async def weekly_summary(self, tasks):
        """Generate task analysis using Claude API."""
        if not tasks:
            return "No tasks to analyze."

        try:
            lines = []
            for t in tasks:
                try:
                    title = to_ascii(t.get("title", "Task")) or "Task"
                    cat = to_ascii(t.get("category", "Personal")) or "Personal"
                    pri = to_ascii(t.get("priority", "Medium")) or "Medium"
                    lines.append(f"- {title} ({cat}, {pri})")
                except Exception:
                    lines.append("- Task (Personal, Medium)")

            if not lines:
                return "No valid tasks to analyze."

            prompt = "Analyze these tasks briefly:\n" + "\n".join(lines) + "\n\nProvide: 1) Overview 2) Top priorities 3) Tips"
            return call_anthropic(prompt)

        except Exception as e:
            err_name = to_ascii(type(e).__name__) or "Error"
            return f"Analysis failed: {err_name}"


# Singleton instance
ai_brain = AIBrain()
