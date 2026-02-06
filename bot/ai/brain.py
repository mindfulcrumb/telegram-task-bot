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
    """Call Anthropic API with full debug info."""
    import requests
    import sys
    import traceback

    debug_info = []

    try:
        # Debug: Check encoding environment
        debug_info.append("enc:" + str(sys.stdout.encoding))

        # Get and clean API key
        raw_key = os.environ.get("ANTHROPIC_API_KEY", "")
        debug_info.append("keylen:" + str(len(raw_key)))

        if not raw_key:
            return "Error: No API key"

        # Clean API key - remove ALL whitespace and control characters
        import re
        # Remove: whitespace, control chars (0x00-0x1f), DEL (0x7f), extended control (0x80-0x9f)
        api_key = re.sub(r'[\s\x00-\x1f\x7f-\x9f]', '', raw_key)
        # Also ensure pure ASCII
        api_key = api_key.encode("ascii", errors="ignore").decode("ascii")
        debug_info.append("cleankey:" + str(len(api_key)))

        if not api_key:
            return "Error: Empty key after clean"

        # Clean prompt
        safe_prompt = to_ascii(prompt_text) or "Analyze tasks"
        debug_info.append("prompt:" + str(len(safe_prompt)))

        # Build body with EXPLICIT ascii encoding
        body_dict = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 500,
            "messages": [{"role": "user", "content": safe_prompt}]
        }

        # Force ASCII JSON - this CANNOT have encoding issues
        body_str = json.dumps(body_dict, ensure_ascii=True)
        body_bytes = body_str.encode("ascii")
        debug_info.append("body:" + str(len(body_bytes)))

        # Make request with raw bytes
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            data=body_bytes,
            timeout=60
        )
        debug_info.append("status:" + str(response.status_code))

        # Parse response safely
        resp_text = response.content.decode("utf-8", errors="replace")
        data = json.loads(resp_text)

        if "error" in data:
            err_msg = str(data.get("error", {}).get("message", ""))
            return "API error: " + to_ascii(err_msg)[:100]

        content = data.get("content", [])
        if content:
            return to_ascii(content[0].get("text", ""))

        return "No response"

    except Exception as e:
        # Get full traceback
        tb = traceback.format_exc()
        # Find the actual error line
        lines = tb.strip().split("\n")
        last_lines = lines[-3:] if len(lines) >= 3 else lines
        err_detail = " | ".join(to_ascii(l.strip())[:50] for l in last_lines)
        return "DEBUG[" + ",".join(debug_info) + "] " + err_detail[:200]


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
