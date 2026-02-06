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
    Call Anthropic API using curl subprocess. 100% encoding-safe.
    Bypasses Python's HTTP stack which has known UnicodeEncodeError issues.
    """
    import subprocess

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "Error: API key not configured"

    # Convert prompt to ASCII
    safe_prompt = to_ascii(prompt_text) or "Analyze tasks"

    # Build JSON body - escape special characters for JSON
    safe_prompt_escaped = safe_prompt.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    json_body = '{"model":"claude-3-5-sonnet-20241022","max_tokens":500,"messages":[{"role":"user","content":"' + safe_prompt_escaped + '"}]}'

    try:
        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                "https://api.anthropic.com/v1/messages",
                "-H", "x-api-key: " + api_key,
                "-H", "anthropic-version: 2023-06-01",
                "-H", "content-type: application/json",
                "-d", json_body
            ],
            capture_output=True,
            timeout=60,
            text=True
        )

        if result.returncode != 0:
            return "Error: curl failed with code " + str(result.returncode)

        # Parse response
        response_data = json.loads(result.stdout)

        # Check for API error
        if "error" in response_data:
            err_msg = response_data.get("error", {}).get("message", "Unknown API error")
            return "API error: " + to_ascii(err_msg)

        content = response_data.get("content", [])
        if content and len(content) > 0:
            raw_text = content[0].get("text", "")
            return to_ascii(raw_text)
        return "No response from AI"

    except subprocess.TimeoutExpired:
        return "Error: Request timed out"
    except json.JSONDecodeError:
        return "Error: Invalid API response"
    except FileNotFoundError:
        return "Error: curl not found"
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
