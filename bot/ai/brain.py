"""AI Brain - Claude-powered intelligence for the task bot."""
import json
import os
from datetime import date

# Get API key directly from environment (bypass config module)
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


def to_ascii(text):
    """Force text to pure ASCII - no exceptions possible."""
    if not text:
        return ""
    out = []
    for c in str(text):
        if ord(c) < 128:
            out.append(c)
    return "".join(out)


def call_anthropic(prompt_text):
    """Call Anthropic API with minimal code. Returns response or error string."""
    import urllib.request
    import urllib.error

    if not ANTHROPIC_KEY:
        return "[ERROR: NO_API_KEY]"

    # Build request body - pure ASCII
    body_dict = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 500,
        "messages": [{"role": "user", "content": to_ascii(prompt_text)}]
    }

    body_str = json.dumps(body_dict, ensure_ascii=True)
    body_bytes = body_str.encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body_bytes,
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        },
        method="POST"
    )

    try:
        import ssl
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return to_ascii(data["content"][0]["text"])
    except urllib.error.HTTPError as e:
        return "[ERROR: HTTP " + str(e.code) + "]"
    except urllib.error.URLError:
        return "[ERROR: NETWORK]"
    except Exception as e:
        return "[ERROR: " + to_ascii(type(e).__name__) + "]"


class AIBrain:
    def __init__(self):
        self.conversation_history = []

    async def process(self, user_input, tasks=None):
        """Process user input - returns fallback action."""
        return {"action": "fallback", "data": {}, "response": None}

    async def weekly_summary(self, tasks):
        """Generate task analysis."""
        if not tasks:
            return "No tasks to analyze."

        # Build task list as pure ASCII
        lines = []
        for t in tasks:
            title = to_ascii(t.get("title", "Untitled"))
            cat = to_ascii(t.get("category", "Personal"))
            pri = to_ascii(t.get("priority", "Medium"))
            lines.append("- " + title + " (" + cat + ", " + pri + ")")

        prompt = "Analyze briefly:\n" + "\n".join(lines) + "\n\nGive: 1) Overview 2) Top priorities 3) Tips"

        result = call_anthropic(prompt)
        return result


ai_brain = AIBrain()
