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
    Call Anthropic API using only stdlib. Returns ASCII string.
    This function catches ALL exceptions and returns safe ASCII error messages.
    """
    import urllib.request
    import urllib.error
    import ssl

    # Get API key
    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    except Exception:
        api_key = ""

    if not api_key:
        return "Error: API key not configured"

    try:
        # Convert prompt to pure ASCII
        safe_prompt = to_ascii(prompt_text)
        if not safe_prompt:
            safe_prompt = "Analyze tasks"

        # Build request body as pure ASCII JSON
        body_dict = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 500,
            "messages": [{"role": "user", "content": safe_prompt}]
        }

        # json.dumps with ensure_ascii=True guarantees ASCII output
        body_json = json.dumps(body_dict, ensure_ascii=True)
        body_bytes = body_json.encode("ascii")

        # Create request
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body_bytes,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            method="POST"
        )

        # Make request with SSL
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            # Read and decode response with error handling
            response_bytes = resp.read()
            response_text = response_bytes.decode("utf-8", errors="replace")
            response_data = json.loads(response_text)

            # Extract content and convert to ASCII
            content = response_data.get("content", [])
            if content and len(content) > 0:
                raw_text = content[0].get("text", "")
                return to_ascii(raw_text)
            return "No response from AI"

    except urllib.error.HTTPError as e:
        try:
            code = e.code
        except Exception:
            code = 0
        return "API error: HTTP " + str(code)
    except urllib.error.URLError:
        return "Network error: Could not connect"
    except json.JSONDecodeError:
        return "Error: Invalid API response"
    except Exception as e:
        # Get exception type name safely
        try:
            err_name = to_ascii(type(e).__name__)
        except Exception:
            err_name = "Unknown"
        if not err_name:
            err_name = "Unknown"
        return "Error: " + err_name


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
