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
    Call Anthropic API using urllib.request with explicit byte handling.
    """
    import urllib.request
    import ssl

    step = "init"
    try:
        step = "get_key"
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return "Error: API key not configured"

        step = "ascii_prompt"
        safe_prompt = to_ascii(prompt_text) or "Analyze tasks"

        step = "build_body"
        body_dict = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 500,
            "messages": [{"role": "user", "content": safe_prompt}]
        }

        step = "json_dumps"
        body_str = json.dumps(body_dict, ensure_ascii=True)

        step = "encode_body"
        body_bytes = body_str.encode("ascii")

        step = "create_request"
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body_bytes,
            method="POST"
        )

        step = "add_headers"
        req.add_header("x-api-key", to_ascii(api_key))
        req.add_header("anthropic-version", "2023-06-01")
        req.add_header("content-type", "application/json")

        step = "ssl_context"
        ctx = ssl.create_default_context()

        step = "urlopen"
        with urllib.request.urlopen(req, context=ctx, timeout=60) as response:
            step = "read_response"
            response_bytes = response.read()

        step = "decode_response"
        response_text = response_bytes.decode("utf-8", errors="replace")

        step = "parse_json"
        data = json.loads(response_text)

        step = "check_error"
        if "error" in data:
            return "API error: " + to_ascii(str(data["error"].get("message", "Unknown")))

        step = "extract"
        content = data.get("content", [])
        if content and len(content) > 0:
            raw_text = content[0].get("text", "")
            return to_ascii(raw_text)

        return "No response from AI"

    except Exception as e:
        return "Error at " + step + ": " + to_ascii(type(e).__name__)


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
