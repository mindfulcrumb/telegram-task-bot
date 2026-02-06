"""AI Brain - Claude-powered intelligence for the task bot."""
import os


def to_ascii(text):
    """Convert text to ASCII safely."""
    if not text:
        return ""
    try:
        return "".join(c for c in str(text) if ord(c) < 128)
    except Exception:
        return ""


def call_anthropic(prompt_text):
    """Call Anthropic API using the official SDK."""
    import anthropic

    # Get API key from config (already cleaned)
    import config
    api_key = config.ANTHROPIC_API_KEY

    if not api_key:
        return "Error: No ANTHROPIC_API_KEY configured"

    try:
        # Use the official SDK - it handles all encoding properly
        client = anthropic.Anthropic(api_key=api_key)

        message = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt_text}]
        )

        # Extract text from response
        if message.content:
            return message.content[0].text

        return "No response from AI"

    except anthropic.AuthenticationError:
        # Show key debug info - first 15 and last 5 chars
        key_preview = f"{api_key[:15]}...{api_key[-5:]}" if len(api_key) > 20 else "too short"
        return f"Invalid key (len={len(api_key)}, preview={key_preview})"
    except anthropic.RateLimitError:
        return "Error: Rate limit exceeded - try again later"
    except anthropic.APIError as e:
        return f"API Error: {to_ascii(str(e))[:100]}"
    except Exception as e:
        return f"Error: {to_ascii(type(e).__name__)}"


class AIBrain:
    """AI Brain for task analysis."""

    def __init__(self):
        self.conversation_history = []

    async def process(self, user_input, tasks=None):
        """Process user input - returns fallback action."""
        return {"action": "fallback", "data": {}, "response": None}

    async def weekly_summary(self, tasks):
        """Generate task analysis using Claude API."""
        if not tasks:
            return "No tasks to analyze."

        try:
            # Build task list as pure ASCII
            lines = []
            for t in tasks:
                try:
                    title = to_ascii(t.get("title", "Task"))
                    if not title:
                        title = "Task"
                    cat = to_ascii(t.get("category", "Personal"))
                    if not cat:
                        cat = "Personal"
                    pri = to_ascii(t.get("priority", "Medium"))
                    if not pri:
                        pri = "Medium"
                    lines.append("- " + title + " (" + cat + ", " + pri + ")")
                except Exception:
                    lines.append("- Task (Personal, Medium)")

            if not lines:
                return "No valid tasks to analyze."

            prompt = "Analyze these tasks briefly:\n" + "\n".join(lines) + "\n\nProvide: 1) Overview 2) Top priorities 3) Tips"

            result = call_anthropic(prompt)
            return result
        except Exception as e:
            try:
                err_name = to_ascii(type(e).__name__)
            except Exception:
                err_name = "Error"
            return "Analysis failed: " + (err_name or "Error")


# Singleton instance
ai_brain = AIBrain()
