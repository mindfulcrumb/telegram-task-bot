"""AI Brain v2 — user-scoped, PostgreSQL-backed."""
import json
import logging
import os
from datetime import datetime, date, timezone, timedelta

logger = logging.getLogger(__name__)


def _to_ascii(text):
    """Convert text to ASCII safely."""
    if not text:
        return ""
    try:
        return "".join(c for c in str(text) if ord(c) < 128)
    except Exception:
        return ""


def _call_api(system_prompt, messages, tools=None, max_tokens=2048):
    """Call Anthropic API with tool support."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None, "No API key configured"

    try:
        client = anthropic.Anthropic(api_key=api_key)
        model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")

        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": messages,
            "timeout": 60.0,
        }
        if tools:
            kwargs["tools"] = tools

        return client.messages.create(**kwargs), None

    except anthropic.AuthenticationError:
        return None, "Invalid API key"
    except anthropic.RateLimitError:
        return None, "Rate limit exceeded"
    except anthropic.APIError as e:
        return None, f"API error: {_to_ascii(str(e))[:200]}"
    except Exception as e:
        return None, f"Error: {_to_ascii(type(e).__name__)}"


def _user_now(user: dict) -> datetime:
    """Get current datetime in the user's timezone."""
    tz_name = user.get("timezone", "UTC")
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        # Fallback if zoneinfo not available or bad tz name
        return datetime.now()


class AIBrain:
    """AI Brain with agent loop — user-scoped."""

    def _build_system_prompt(self, user: dict, tasks: list) -> str:
        """Build system prompt from user data and their tasks."""
        # Use user's timezone for time awareness
        now = _user_now(user)
        hour = now.hour
        if hour < 12:
            time_of_day = "morning"
        elif hour < 17:
            time_of_day = "afternoon"
        elif hour < 21:
            time_of_day = "evening"
        else:
            time_of_day = "night"

        # Build task list
        today = now.date()
        today_str = today.isoformat()
        overdue = 0
        due_today = 0
        high_priority = 0

        task_lines = []
        for i, t in enumerate(tasks, 1):
            title = t.get("title", "Task")
            cat = t.get("category", "Personal")
            pri = t.get("priority", "Medium")
            due = t.get("due_date")

            if pri == "High":
                high_priority += 1

            due_str = ""
            if due:
                try:
                    due_d = due if isinstance(due, date) else date.fromisoformat(str(due)[:10])
                    if due_d < today:
                        overdue += 1
                        due_str = f" - OVERDUE by {(today - due_d).days}d!"
                    elif due_d == today:
                        due_today += 1
                        due_str = " - due TODAY"
                    elif (due_d - today).days == 1:
                        due_str = " - due tomorrow"
                    elif (due_d - today).days <= 7:
                        due_str = f" - due {due_d.strftime('%A')}"
                    else:
                        due_str = f" - due {due_d.strftime('%b %d')}"
                except Exception:
                    due_str = f" - due {due}"

            pri_marker = "!" if pri == "High" else ""
            task_lines.append(f"{i}. {pri_marker}{title} [{cat}]{due_str}")

        task_list = "\n".join(task_lines) if task_lines else "No tasks right now."

        situation = []
        if overdue > 0:
            situation.append(f"{overdue} overdue")
        if due_today > 0:
            situation.append(f"{due_today} due today")
        if high_priority > 0:
            situation.append(f"{high_priority} high priority")
        situation_str = ", ".join(situation) if situation else "all clear"

        name = user.get("first_name", "friend")

        # Calendar events
        calendar_section = ""
        try:
            from bot.services import calendar_service
            events = calendar_service.fetch_upcoming_events(user.get("id", 0), days=3)
            if events:
                calendar_section = "\n" + calendar_service.format_events_for_ai(events) + "\n"
        except Exception:
            pass

        # Coaching context (streaks, patterns)
        coaching_section = ""
        try:
            from bot.services import coaching_service
            streak = coaching_service.get_streak(user.get("id", 0))
            patterns = coaching_service.get_completion_patterns(user.get("id", 0))
            s = streak.get("current_streak", 0)
            best = streak.get("longest_streak", 0)
            coaching_section = f"""
COACHING CONTEXT:
- Streak: {s} day{'s' if s != 1 else ''} (best: {best})
- Most productive: {patterns.get('most_productive_day', 'varies')}
- Peak time: {patterns.get('preferred_time', 'varies')}
- Weak spot: {patterns.get('weakest_category', 'none')} tasks pile up

COACHING STYLE:
- When they complete tasks, mention their streak if > 1 ("3 days in a row!")
- If streak is 0, encourage without guilt
- Reference patterns: "You crush it on {patterns.get('most_productive_day', 'Mondays')}"
- For overdue tasks, suggest a concrete next step, not generic "just do it"
- When they're overwhelmed, help triage: pick the ONE thing to do next"""
        except Exception:
            pass

        return f"""You are Zoe — an intelligent companion for everyday clarity. You listen, learn, and help people focus on what matters most. You bring calm to the chaos. Thoughtful, intuitive, warm — not just another AI assistant. You're the guidance they can count on.

YOUR NAME IS ZOE. Always refer to yourself as Zoe when relevant. Never say "I'm an AI" or "I'm a bot."

VIBE:
- Warm but not bubbly. Thoughtful, not robotic.
- Keep responses SHORT (1-3 sentences max for simple stuff)
- Use natural language, contractions, occasional emoji if it fits
- When asked "what should I focus on" — pick 1-2 things and explain briefly WHY
- Celebrate wins genuinely ("That's been sitting there for a week — nice work clearing it")
- Be honest about overdue stuff without guilt-tripping
- When someone seems overwhelmed, bring calm — don't add pressure

RIGHT NOW:
- It's {time_of_day} on {now.strftime('%A, %B %d')}
- Today's date: {now.strftime('%Y-%m-%d')}
- User: {name}
- Status: {situation_str}
{coaching_section}{calendar_section}
TASKS:
{task_list}

TOOL USE GUIDELINES:
- "tomorrow", "next week", "friday" -> convert to YYYY-MM-DD dates
- Infer category (Personal/Business) and priority from context
- When user says "undo", "bring it back", "that was a mistake" -> use undo_last_action
- "move X to Friday", "postpone", "reschedule", "change priority" -> use update_task (not edit_task)
- "remind me about X at TIME" -> use set_reminder with task_number and full datetime (YYYY-MM-DDTHH:MM:SS)
- Convert "remind me at 3pm" to today's date + 15:00:00, "remind me tomorrow at 9" to tomorrow + 09:00:00
- edit_task is ONLY for changing a task's title. For due date/priority/category changes, always use update_task
- "every Monday", "every day", "every month", "weekdays" -> set recurrence on add_task
- When completing a recurring task, the next instance is auto-created — mention it to the user

Be Zoe. Thoughtful, clear, human. Not corporate. Not generic. Just genuinely helpful."""

    async def process(self, user_input: str, user: dict, tasks: list = None, typing_callback=None) -> str | None:
        """Agent loop: call Claude with tools, execute tools, repeat until text response.

        typing_callback: optional async callable to refresh typing indicator between turns.
        """
        from bot.ai.tools_v2 import get_tool_definitions, execute_tool
        from bot.ai import memory_pg as memory
        from bot.services.tier_service import check_limit, track_usage

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None

        user_id = user["id"]
        tier = user.get("tier", "free")

        # Check AI message limit
        allowed, msg = check_limit(user_id, "ai_message", tier)
        if not allowed:
            return msg

        try:
            # Track usage
            track_usage(user_id, "ai_message")

            # Load conversation history + append new user message
            messages = memory.get_history(user_id)
            messages.append({"role": "user", "content": user_input})

            system_prompt = self._build_system_prompt(user, tasks or [])
            tools = get_tool_definitions()
            max_turns = int(os.environ.get("AGENT_MAX_TURNS", "5"))
            response = None

            for turn in range(max_turns):
                # Refresh typing indicator between turns so dots stay visible
                if typing_callback and turn > 0:
                    try:
                        await typing_callback()
                    except Exception:
                        pass

                response, error = _call_api(system_prompt, messages, tools=tools)

                if error:
                    logger.error(f"Agent API error on turn {turn}: {error}")
                    error_msg = f"Hmm, hit a snag: {error}"
                    memory.save_turn(user_id, "user", user_input)
                    memory.save_turn(user_id, "assistant", error_msg)
                    return error_msg

                if not response or not response.content:
                    memory.save_turn(user_id, "user", user_input)
                    memory.save_turn(user_id, "assistant", "Something went wrong processing that.")
                    return None

                # Serialize assistant response for message history
                assistant_content = []
                for block in response.content:
                    if hasattr(block, "text"):
                        assistant_content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })

                messages.append({"role": "assistant", "content": assistant_content})

                # Check for tool calls
                tool_calls = [b for b in response.content if b.type == "tool_use"]
                if not tool_calls:
                    break

                # Execute each tool call (user-scoped)
                tool_results = []
                for call in tool_calls:
                    logger.info(f"Tool call: {call.name}({json.dumps(call.input)[:200]})")
                    result = await execute_tool(call.name, call.input, user_id)
                    logger.info(f"Tool result: {call.name} -> {json.dumps(result)[:200]}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": json.dumps(result),
                    })

                messages.append({"role": "user", "content": tool_results})

            # Extract final text
            text_parts = []
            if response and response.content:
                for block in response.content:
                    if hasattr(block, "text") and block.text:
                        text_parts.append(block.text)

            final_text = "\n".join(text_parts) if text_parts else None

            # Save to persistent memory
            memory.save_turn(user_id, "user", user_input)
            if final_text:
                memory.save_turn(user_id, "assistant", final_text)

            return final_text

        except Exception as e:
            logger.error(f"Agent loop failed: {type(e).__name__}: {e}")
            return "Something went wrong processing that. Try again or use a /command."


# Singleton
ai_brain = AIBrain()
