"""AI Brain - Claude-powered intelligence for the task bot."""
import json
import os


def to_ascii(text):
    """
    Convert any text to pure ASCII by removing non-ASCII characters.
    This function CANNOT raise any exception - it is 100% safe.
    """
    if text is None:
        return ""
    try:
        s = str(text)
    except Exception:
        return ""
    try:
        result = []
        for char in s:
            try:
                code = ord(char)
                if code < 128:
                    result.append(char)
            except Exception:
                pass
        return "".join(result)
    except Exception:
        return ""


def call_anthropic(prompt_text):
    """
    Call Anthropic API using httpx with EXPLICIT ASCII JSON encoding.
    This bypasses all Python encoding issues by using ensure_ascii=True.
    """
    import httpx

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "Error: API key not configured"

    # Convert prompt to ASCII FIRST
    safe_prompt = to_ascii(prompt_text) or "Analyze tasks"

    # Build request with pure ASCII data
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    body = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 500,
        "messages": [{"role": "user", "content": safe_prompt}]
    }

    try:
        # CRITICAL: Use json.dumps with ensure_ascii=True to force ASCII encoding
        # This prevents ANY unicode from reaching httpx internals
        json_body = json.dumps(body, ensure_ascii=True)

        # Use httpx with explicit timeout, pass raw bytes
        with httpx.Client(timeout=60.0) as client:
            response = client.post(url, headers=headers, content=json_body.encode("ascii"))

        # Parse response
        response_data = response.json()

        # Check for API error
        if "error" in response_data:
            err_msg = response_data.get("error", {}).get("message", "Unknown API error")
            return "API error: " + to_ascii(err_msg)

        content = response_data.get("content", [])
        if content and len(content) > 0:
            raw_text = content[0].get("text", "")
            return to_ascii(raw_text)
        return "No response from AI"

    except httpx.TimeoutException:
        return "Error: Request timed out"
    except Exception as e:
        return "Error: " + to_ascii(type(e).__name__)


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
