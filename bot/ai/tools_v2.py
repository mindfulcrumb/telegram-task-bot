"""MVP tool definitions and executor — user-scoped, PostgreSQL-backed."""
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Undo buffer (in-memory, keyed by user_id)
_undo_buffer = {}


def get_tool_definitions() -> list:
    """Return MVP tool definitions for Claude tool_use."""
    return [
        {
            "name": "get_tasks",
            "description": "Get the user's current tasks. Use this when they ask to see tasks, what's pending, what's due, etc.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "enum": ["all", "today", "business", "personal", "overdue", "week"],
                        "description": "Filter tasks. 'all' returns everything."
                    }
                },
                "required": ["filter"]
            }
        },
        {
            "name": "add_task",
            "description": "Create a new task. Infer category (Personal/Business) and priority from context. Set recurrence for repeating tasks.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task title"},
                    "category": {"type": "string", "enum": ["Personal", "Business"]},
                    "priority": {"type": "string", "enum": ["Low", "Medium", "High"]},
                    "due_date": {"type": "string", "description": "Due date in YYYY-MM-DD format, or null"},
                    "recurrence": {"type": "string", "enum": ["daily", "weekdays", "weekly", "monthly"], "description": "Repeat pattern. Use when user says 'every day', 'every Monday', 'every month', 'weekdays', etc."}
                },
                "required": ["title"]
            }
        },
        {
            "name": "complete_tasks",
            "description": "Mark one or more tasks as done by their task numbers from the list.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_numbers": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "List of task numbers to mark as done"
                    }
                },
                "required": ["task_numbers"]
            }
        },
        {
            "name": "delete_tasks",
            "description": "Delete one or more tasks by their task numbers from the list.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_numbers": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "List of task numbers to delete"
                    }
                },
                "required": ["task_numbers"]
            }
        },
        {
            "name": "undo_last_action",
            "description": "Undo the last delete or done action, restoring the affected tasks.",
            "input_schema": {"type": "object", "properties": {}}
        },
        {
            "name": "edit_task",
            "description": "Edit a task's title only. For changing due date, priority, or category, use update_task instead.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_number": {"type": "integer", "description": "Task number to edit"},
                    "new_title": {"type": "string", "description": "New title for the task"}
                },
                "required": ["task_number", "new_title"]
            }
        },
        {
            "name": "update_task",
            "description": "Update a task's due date, priority, or category. Use when user says 'move X to Friday', 'make it high priority', 'change category to business', 'reschedule', 'postpone', etc.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_number": {"type": "integer", "description": "Task number to update"},
                    "due_date": {"type": "string", "description": "New due date in YYYY-MM-DD format. Use null to clear."},
                    "priority": {"type": "string", "enum": ["Low", "Medium", "High"], "description": "New priority level"},
                    "category": {"type": "string", "enum": ["Personal", "Business"], "description": "New category"},
                    "title": {"type": "string", "description": "New title (optional)"}
                },
                "required": ["task_number"]
            }
        },
        {
            "name": "set_reminder",
            "description": "Set a reminder on a task. The bot will send a message at the specified time. Use when user says 'remind me', 'set a reminder', 'alert me at', etc.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_number": {"type": "integer", "description": "Task number to set reminder on"},
                    "reminder_datetime": {"type": "string", "description": "When to remind, in ISO format YYYY-MM-DDTHH:MM:SS. Convert 'tomorrow at 9am' etc. to full datetime."}
                },
                "required": ["task_number", "reminder_datetime"]
            }
        },
        # --- Fitness tools ---
        {
            "name": "log_workout",
            "description": "Log a workout session. Use when user says they trained, worked out, did exercises, went to the gym, etc. Infer movement_pattern from exercise names. Exercises array is optional for quick logs like 'did cardio for 30 min'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Workout title (e.g., 'Upper Body Push', 'Leg Day', 'Cardio')"},
                    "duration_minutes": {"type": "integer", "description": "Duration in minutes"},
                    "rpe": {"type": "number", "description": "Rate of perceived exertion 1-10"},
                    "notes": {"type": "string", "description": "Any notes (how it felt, pain, energy level)"},
                    "exercises": {
                        "type": "array",
                        "description": "Individual exercises performed. Include when user mentions specific exercises with sets/reps/weight.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "exercise_name": {"type": "string", "description": "Exercise name (e.g., 'Bench Press', 'Squat')"},
                                "movement_pattern": {"type": "string", "enum": ["squat", "hinge", "horizontal_push", "horizontal_pull", "vertical_push", "vertical_pull", "carry_rotation"], "description": "Movement pattern category. Infer from exercise name if not specified."},
                                "sets": {"type": "integer", "description": "Number of sets"},
                                "reps": {"type": "string", "description": "Reps per set (e.g., '8', '8-10', '12,10,8')"},
                                "weight": {"type": "number", "description": "Weight used"},
                                "weight_unit": {"type": "string", "enum": ["kg", "lbs"], "description": "Weight unit (default: kg)"},
                                "rpe": {"type": "number", "description": "RPE for this exercise specifically"}
                            },
                            "required": ["exercise_name"]
                        }
                    }
                },
                "required": ["title"]
            }
        },
        {
            "name": "get_fitness_context",
            "description": "Get full fitness summary: recent workouts, movement pattern balance, streak, body metrics, PRs, volume trends. Call this BEFORE giving workout advice or when user asks 'what should I train', 'how am I doing', 'program my week', etc.",
            "input_schema": {"type": "object", "properties": {}}
        },
        {
            "name": "log_body_metric",
            "description": "Log a body metric. Use when user mentions their weight, body fat, measurements, or 1RM numbers.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "metric_type": {"type": "string", "description": "Type of metric (weight, body_fat, chest, waist, hips, arms, bench_1rm, squat_1rm, deadlift_1rm, or custom)"},
                    "value": {"type": "number", "description": "The measurement value"},
                    "unit": {"type": "string", "description": "Unit of measurement (kg, lbs, %, cm, in)"}
                },
                "required": ["metric_type", "value"]
            }
        },
        {
            "name": "update_fitness_profile",
            "description": "Set or update user's fitness profile: goals, experience level, training frequency, limitations, preferred style. Use when user mentions their fitness goal, experience, injuries/limitations, or how often they train.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "fitness_goal": {"type": "string", "enum": ["build_muscle", "lose_fat", "strength", "athletic_performance", "general_fitness"], "description": "Primary fitness goal"},
                    "experience_level": {"type": "string", "enum": ["beginner", "intermediate", "advanced"], "description": "Training experience level"},
                    "training_days_per_week": {"type": "integer", "description": "How many days per week they train"},
                    "limitations": {"type": "string", "description": "Physical limitations or injuries (e.g., 'bad left shoulder', 'lower back issues')"},
                    "preferred_style": {"type": "string", "description": "Training style preference (e.g., 'powerlifting', 'calisthenics', 'functional', 'bodybuilding')"}
                }
            }
        },
        {
            "name": "get_exercise_history",
            "description": "Get progression history for a specific exercise. Use when user asks about their progress on a specific lift, or when you need data to program progressive overload.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "exercise_name": {"type": "string", "description": "Exercise name to look up (e.g., 'Bench Press', 'Squat')"},
                    "limit": {"type": "integer", "description": "Number of recent entries to return (default: 10)"}
                },
                "required": ["exercise_name"]
            }
        },
    ]


async def execute_tool(name: str, args: dict, user_id: int) -> dict:
    """Execute a tool scoped to user_id."""
    try:
        from bot.services import task_service

        if name == "get_tasks":
            filter_type = args.get("filter", "all")
            tasks = task_service.get_tasks(user_id, filter_type)
            if not tasks:
                return {"tasks": [], "message": "No tasks found."}
            task_list = []
            for t in tasks:
                entry = {
                    "number": t["index"],
                    "title": t["title"],
                    "category": t["category"],
                    "priority": t["priority"],
                }
                if t.get("due_date"):
                    entry["due_date"] = t["due_date"].isoformat() if hasattr(t["due_date"], "isoformat") else str(t["due_date"])
                if t.get("recurrence"):
                    entry["recurrence"] = t["recurrence"]
                task_list.append(entry)
            return {"tasks": task_list, "count": len(task_list)}

        elif name == "add_task":
            due_date = None
            if args.get("due_date"):
                try:
                    due_date = datetime.fromisoformat(args["due_date"]).date()
                except (ValueError, TypeError):
                    pass
            recurrence = args.get("recurrence")
            task_service.add_task(
                user_id=user_id,
                title=args["title"],
                category=args.get("category", "Personal"),
                priority=args.get("priority", "Medium"),
                due_date=due_date,
                recurrence=recurrence,
            )
            result = {"success": True, "title": args["title"]}
            if recurrence:
                result["recurrence"] = recurrence
            return result

        elif name == "complete_tasks":
            task_nums = args.get("task_numbers", [])
            completed, not_found = task_service.complete_tasks(user_id, task_nums)
            _undo_buffer[user_id] = [{"action": "done", "task_id": t["id"], "title": t["title"]} for t in completed]
            # Update streak + spawn recurring tasks
            recurring_spawned = []
            if completed:
                try:
                    from bot.services import coaching_service
                    streak = coaching_service.update_streak(user_id)
                    result = {
                        "completed": [t["title"] for t in completed],
                        "streak": streak.get("current_streak", 0),
                    }
                except Exception:
                    result = {"completed": [t["title"] for t in completed]}
                # Spawn next instance for recurring tasks
                for t in completed:
                    spawned = task_service.spawn_next_recurring(user_id, t)
                    if spawned:
                        recurring_spawned.append(f"{spawned['title']} (next: {spawned['due_date']})")
                if recurring_spawned:
                    result["recurring_created"] = recurring_spawned
            else:
                result = {"completed": []}
            if not_found:
                result["not_found"] = not_found
            return result

        elif name == "delete_tasks":
            task_nums = args.get("task_numbers", [])
            deleted, not_found = task_service.delete_tasks(user_id, task_nums)
            _undo_buffer[user_id] = [{"action": "delete", "task_id": t["id"], "title": t["title"]} for t in deleted]
            result = {"deleted": [t["title"] for t in deleted]}
            if not_found:
                result["not_found"] = not_found
            return result

        elif name == "undo_last_action":
            entries = _undo_buffer.pop(user_id, None)
            if not entries:
                return {"message": "Nothing to undo."}
            task_ids = [e["task_id"] for e in entries]
            restored = task_service.restore_tasks(user_id, task_ids)
            return {"restored": restored}

        elif name == "edit_task":
            result = task_service.update_task_title(user_id, args["task_number"], args["new_title"])
            if result:
                return {"old_title": result[0], "new_title": result[1]}
            return {"error": f"Task #{args['task_number']} not found."}

        elif name == "update_task":
            updates = {}
            if args.get("due_date"):
                try:
                    updates["due_date"] = datetime.fromisoformat(args["due_date"]).date()
                except (ValueError, TypeError):
                    return {"error": "Invalid date format. Use YYYY-MM-DD."}
            if args.get("priority"):
                updates["priority"] = args["priority"]
            if args.get("category"):
                updates["category"] = args["category"]
            if args.get("title"):
                updates["title"] = args["title"]
            if not updates:
                return {"error": "No changes specified."}
            result = task_service.update_task(user_id, args["task_number"], **updates)
            if result:
                changes = ", ".join(f"{k}={v}" for k, v in updates.items())
                return {"success": True, "task": result["title"], "changes": changes}
            return {"error": f"Task #{args['task_number']} not found."}

        elif name == "set_reminder":
            from bot.services.tier_service import check_limit
            user_tier = "free"
            try:
                from bot.services import user_service
                u = user_service.get_user_by_id(user_id)
                user_tier = u.get("tier", "free") if u else "free"
            except Exception:
                pass
            allowed, limit_msg = check_limit(user_id, "set_reminder", user_tier)
            if not allowed:
                return {"error": limit_msg}
            try:
                reminder_dt = datetime.fromisoformat(args["reminder_datetime"])
            except (ValueError, TypeError):
                return {"error": "Invalid datetime format. Use YYYY-MM-DDTHH:MM:SS"}
            if reminder_dt <= datetime.now():
                return {"error": "Reminder time must be in the future."}
            ok = task_service.set_reminder(user_id, args["task_number"], reminder_dt)
            if ok:
                tasks = task_service.get_tasks(user_id)
                idx = args["task_number"]
                title = tasks[idx - 1]["title"] if 1 <= idx <= len(tasks) else "task"
                return {"success": True, "task": title, "remind_at": reminder_dt.strftime("%b %d at %I:%M %p")}
            return {"error": f"Task #{args['task_number']} not found."}

        # --- Fitness tools ---
        elif name == "log_workout":
            from bot.services import fitness_service
            workout = fitness_service.log_workout(
                user_id=user_id,
                title=args["title"],
                duration_minutes=args.get("duration_minutes"),
                rpe=args.get("rpe"),
                notes=args.get("notes"),
                exercises=args.get("exercises"),
            )
            result = {
                "success": True,
                "title": args["title"],
                "workout_id": workout["id"],
            }
            if args.get("duration_minutes"):
                result["duration"] = args["duration_minutes"]
            if args.get("exercises"):
                result["exercise_count"] = len(args["exercises"])
            # Include streak info
            streak = fitness_service.get_workout_streak(user_id)
            result["workout_streak"] = streak.get("current_streak", 0)
            # Include any PRs detected
            if workout.get("prs"):
                result["prs"] = workout["prs"]
            # Include pattern balance for AI to reference
            patterns = fitness_service.get_movement_pattern_balance(user_id, days=14)
            if patterns:
                result["pattern_balance_14d"] = patterns
            return result

        elif name == "get_fitness_context":
            from bot.services import fitness_service
            summary = fitness_service.get_fitness_summary(user_id)
            # Format for AI consumption
            result = {}
            if summary["profile"]:
                p = summary["profile"]
                result["profile"] = {
                    "goal": p.get("fitness_goal"),
                    "experience": p.get("experience_level"),
                    "days_per_week": p.get("training_days_per_week"),
                    "limitations": p.get("limitations"),
                    "style": p.get("preferred_style"),
                }
            s = summary["streak"]
            result["streak"] = {
                "current": s.get("current_streak", 0),
                "longest": s.get("longest_streak", 0),
                "last_workout": s["last_workout_date"].isoformat() if s.get("last_workout_date") else None,
                "weekly_target": s.get("weekly_target", 3),
            }
            # Recent workouts with exercises
            recent = []
            for w in summary["recent_workouts"]:
                entry = {
                    "date": w["created_at"].strftime("%Y-%m-%d") if w.get("created_at") else None,
                    "title": w["title"],
                    "duration": w.get("duration_minutes"),
                    "rpe": w.get("rpe"),
                }
                if w.get("exercises"):
                    entry["exercises"] = [
                        {
                            "name": ex["exercise_name"],
                            "pattern": ex.get("movement_pattern"),
                            "sets": ex.get("sets"),
                            "reps": ex.get("reps"),
                            "weight": ex.get("weight"),
                            "unit": ex.get("weight_unit"),
                        }
                        for ex in w["exercises"]
                    ]
                recent.append(entry)
            result["recent_workouts"] = recent
            result["pattern_balance_14d"] = summary["pattern_balance"]
            vol = summary["volume_trend"]
            result["volume_trend"] = {
                "trend": vol["trend"],
                "this_week_sets": vol["this_week_sets"],
                "last_week_sets": vol["last_week_sets"],
            }
            # Metrics
            metrics = {}
            for k, v in summary["latest_metrics"].items():
                metrics[k] = {
                    "value": v["value"],
                    "unit": v.get("unit"),
                    "date": v["recorded_at"].strftime("%Y-%m-%d") if v.get("recorded_at") else None,
                }
            result["latest_metrics"] = metrics
            result["recent_prs"] = summary["recent_prs"]
            result["active_training_weeks"] = summary["active_training_weeks"]
            return result

        elif name == "log_body_metric":
            from bot.services import fitness_service
            metric = fitness_service.log_metric(
                user_id=user_id,
                metric_type=args["metric_type"],
                value=args["value"],
                unit=args.get("unit"),
            )
            result = {
                "success": True,
                "metric_type": args["metric_type"],
                "value": args["value"],
                "unit": args.get("unit"),
            }
            if metric.get("previous_value") is not None:
                result["previous_value"] = metric["previous_value"]
                result["change"] = metric["change"]
            return result

        elif name == "update_fitness_profile":
            from bot.services import fitness_service
            profile = fitness_service.update_fitness_profile(user_id, **args)
            return {
                "success": True,
                "profile": {
                    "goal": profile.get("fitness_goal"),
                    "experience": profile.get("experience_level"),
                    "days_per_week": profile.get("training_days_per_week"),
                    "limitations": profile.get("limitations"),
                    "style": profile.get("preferred_style"),
                }
            }

        elif name == "get_exercise_history":
            from bot.services import fitness_service
            history = fitness_service.get_exercise_history(
                user_id=user_id,
                exercise_name=args["exercise_name"],
                limit=args.get("limit", 10),
            )
            entries = []
            for h in history:
                entries.append({
                    "date": h["workout_date"].strftime("%Y-%m-%d") if h.get("workout_date") else None,
                    "sets": h.get("sets"),
                    "reps": h.get("reps"),
                    "weight": h.get("weight"),
                    "unit": h.get("weight_unit"),
                    "rpe": h.get("rpe"),
                })
            return {"exercise": args["exercise_name"], "history": entries, "count": len(entries)}

        else:
            return {"error": f"Unknown tool: {name}"}

    except Exception as e:
        logger.error(f"Tool {name} failed: {type(e).__name__}: {e}")
        return {"error": f"Tool failed: {type(e).__name__}: {str(e)[:100]}"}
