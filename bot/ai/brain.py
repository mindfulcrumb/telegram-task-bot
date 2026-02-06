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
    Call Anthropic API in a SEPARATE SUBPROCESS to completely isolate encoding issues.
    This is the nuclear option that bypasses ALL Python encoding problems.
    """
    import subprocess
    import sys

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "Error: API key not configured"

    safe_prompt = to_ascii(prompt_text) or "Analyze tasks"

    # Python script that runs in subprocess with forced ASCII output
    script = '''
import os, sys, json
sys.stdout.reconfigure(encoding='ascii', errors='replace') if hasattr(sys.stdout, 'reconfigure') else None
sys.stderr.reconfigure(encoding='ascii', errors='replace') if hasattr(sys.stderr, 'reconfigure') else None
try:
    import anthropic
    c = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    m = c.messages.create(model="claude-3-5-sonnet-20241022", max_tokens=500, messages=[{"role":"user","content":sys.argv[1]}])
    t = m.content[0].text if m.content else ""
    print(''.join(x if ord(x)<128 else ' ' for x in t))
except Exception as e:
    print("ERROR:" + type(e).__name__)
'''

    try:
        result = subprocess.run(
            [sys.executable, '-c', script, safe_prompt],
            capture_output=True,
            text=True,
            timeout=90,
            env={
                **os.environ,
                'PYTHONUTF8': '1',
                'PYTHONIOENCODING': 'ascii:replace',
                'PYTHONLEGACYWINDOWSSTDIO': '0'
            }
        )
        output = result.stdout.strip()
        if output.startswith("ERROR:"):
            return "Error: " + output[6:]
        return output or "No response from AI"
    except subprocess.TimeoutExpired:
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
