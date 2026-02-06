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
    Call Anthropic API in isolated subprocess to completely bypass encoding issues.
    The subprocess has its own controlled encoding environment.
    """
    import subprocess
    import sys

    # Clean inputs in main process
    raw_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not raw_key:
        return "Error: No API key"
    api_key = "".join(c for c in raw_key if c.isalnum() or c in "-_")
    if not api_key:
        return "Error: Invalid key"

    safe_prompt = to_ascii(prompt_text) or "Analyze tasks"

    # Self-contained Python script that runs in subprocess
    script = '''
import sys, json, http.client, ssl
key, prompt = sys.argv[1], sys.argv[2]
try:
    body = json.dumps({"model":"claude-3-5-sonnet-20241022","max_tokens":500,"messages":[{"role":"user","content":prompt}]}, ensure_ascii=True).encode("ascii")
    ctx = ssl.create_default_context()
    c = http.client.HTTPSConnection("api.anthropic.com", context=ctx, timeout=60)
    c.request("POST", "/v1/messages", body=body, headers={"x-api-key":key,"anthropic-version":"2023-06-01","content-type":"application/json"})
    r = c.getresponse()
    d = json.loads(r.read().decode("utf-8", errors="replace"))
    c.close()
    if "error" in d:
        print("ERR:" + str(d.get("error",{}).get("message",""))[:200])
    else:
        t = d.get("content",[{}])[0].get("text","") if d.get("content") else ""
        print("OK:" + "".join(x if ord(x)<128 else "" for x in t))
except Exception as e:
    print("ERR:" + type(e).__name__)
'''

    try:
        result = subprocess.run(
            [sys.executable, "-c", script, api_key, safe_prompt],
            capture_output=True,
            timeout=90,
            text=True,
            env={**os.environ, "PYTHONIOENCODING": "ascii:replace", "PYTHONUTF8": "0"}
        )

        output = (result.stdout or "").strip()
        if output.startswith("OK:"):
            return output[3:] or "Analysis complete (no details)"
        elif output.startswith("ERR:"):
            return "API error: " + output[4:]
        else:
            return "Error: " + to_ascii(result.stderr or "Unknown")[:100]

    except subprocess.TimeoutExpired:
        return "Error: Timeout"
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
