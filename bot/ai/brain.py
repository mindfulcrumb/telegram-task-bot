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
    Call Anthropic API with explicit byte handling to avoid ALL encoding issues.
    Uses raw bytes for request/response to bypass Python's encoding layer.
    """
    import requests

    step = "init"
    try:
        # Step 1: Get and clean API key - ONLY alphanumeric and dash/underscore
        step = "key"
        raw_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not raw_key:
            return "Error: No API key"
        api_key = "".join(c for c in raw_key if c.isalnum() or c in "-_")
        if not api_key:
            return "Error: Invalid key"

        # Step 2: Clean prompt to pure ASCII
        step = "prompt"
        safe_prompt = to_ascii(prompt_text) or "Analyze tasks"

        # Step 3: Build JSON body with GUARANTEED ASCII encoding
        step = "body"
        body_dict = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 500,
            "messages": [{"role": "user", "content": safe_prompt}]
        }
        # json.dumps with ensure_ascii=True guarantees pure ASCII output
        body_str = json.dumps(body_dict, ensure_ascii=True)
        body_bytes = body_str.encode("ascii")  # Safe because ensure_ascii=True

        # Step 4: Make request with raw bytes (not json= parameter)
        step = "request"
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            data=body_bytes,  # Send raw bytes, NOT json=
            timeout=60
        )

        # Step 5: Get response as raw bytes and decode safely
        step = "response"
        response_bytes = response.content
        response_text = response_bytes.decode("utf-8", errors="replace")

        # Step 6: Parse JSON from safe string
        step = "parse"
        data = json.loads(response_text)

        # Step 7: Extract result
        step = "extract"
        if "error" in data:
            err_msg = data.get("error", {}).get("message", "Unknown")
            return "API error: " + to_ascii(str(err_msg))

        content = data.get("content", [])
        if content and len(content) > 0:
            raw_text = content[0].get("text", "")
            return to_ascii(raw_text)

        return "No response"

    except requests.Timeout:
        return "Error: Timeout"
    except Exception as e:
        err = "Unknown"
        try:
            err = to_ascii(type(e).__name__)
        except:
            pass
        return "Err@" + step + ":" + err


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
