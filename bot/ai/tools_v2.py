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
        # --- Interactive workout session ---
        {
            "name": "start_workout_session",
            "description": "Start an interactive workout session with tappable set tracking and rest timers. Use this when you are prescribing a specific workout for the user to do RIGHT NOW. Each exercise becomes a card with buttons to mark sets done and start rest timers. Do NOT use this for logging past workouts (use log_workout instead). Keep your text response to 1-2 lines of coaching context — the interactive cards will appear automatically after your message.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Session title (e.g., 'Upper Pull', 'Leg Day', 'Full Body')"},
                    "exercises": {
                        "type": "array",
                        "description": "Exercises in order. Each becomes an interactive card with set tracking.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "exercise_name": {"type": "string", "description": "Exercise name"},
                                "sets": {"type": "integer", "description": "Number of sets"},
                                "reps": {"type": "string", "description": "Target reps (e.g., '6', '8-10', '12')"},
                                "weight": {"type": "number", "description": "Target weight. Use their last known weight for this exercise if available."},
                                "weight_unit": {"type": "string", "enum": ["kg", "lbs"], "description": "Weight unit (default: kg)"},
                                "rpe": {"type": "number", "description": "Target RPE for this exercise"},
                                "notes": {"type": "string", "description": "Coaching cue (e.g., '3s eccentric', 'pause at bottom', 'explosive concentric')"}
                            },
                            "required": ["exercise_name", "sets", "reps"]
                        }
                    }
                },
                "required": ["title", "exercises"]
            }
        },
        # --- Interactive protocol wizard & dashboard ---
        {
            "name": "start_protocol_wizard",
            "description": "Launch the interactive protocol wizard — a guided card-based flow for creating a new peptide protocol. Use when user says 'start a protocol', 'add a peptide', 'set up BPC-157', 'new protocol', or wants to create a structured protocol. The wizard walks them through peptide selection, dose, frequency, route, schedule times, and cycle length via inline buttons. Keep your text response to 1-2 lines — the interactive card appears automatically. Prefer this over manage_peptide_protocol action=add for new protocols.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "peptide_hint": {
                        "type": "string",
                        "description": "Optional: if the user mentioned a specific peptide name, pass it here to pre-select it in the wizard (e.g. 'BPC-157', 'Ipamorelin')"
                    }
                }
            }
        },
        {
            "name": "get_protocol_dashboard",
            "description": "Show the interactive protocol dashboard with progress bars, today's dose status, 7-day adherence visual, and action buttons (log dose, pause, new protocol). Use when user asks 'how are my protocols?', 'show my protocols', 'protocol status', '/protocols', or wants to see their peptide tracking overview. The dashboard card appears automatically — keep your text response to 1-2 lines.",
            "input_schema": {
                "type": "object",
                "properties": {}
            }
        },
        # --- Biohacking tools ---
        {
            "name": "manage_peptide_protocol",
            "description": "Add, pause, resume, or end a peptide protocol. For ADDING new protocols, prefer start_protocol_wizard instead (guided interactive flow). Use this tool directly for pause, resume, and end actions, or when the user provides ALL details in one message and doesn't need the wizard.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "pause", "resume", "end"], "description": "Action to take on the protocol"},
                    "peptide_name": {"type": "string", "description": "Name of the peptide (e.g., 'BPC-157', 'TB-500', 'Ipamorelin')"},
                    "dose_amount": {"type": "number", "description": "Dose amount per administration"},
                    "dose_unit": {"type": "string", "description": "Unit (mcg, mg, IU). Default: mcg"},
                    "frequency": {"type": "string", "description": "How often (e.g., '2x daily', '3x weekly', 'daily')"},
                    "route": {"type": "string", "description": "Administration route (subcutaneous, intramuscular, nasal, oral). Default: subcutaneous"},
                    "cycle_start": {"type": "string", "description": "Cycle start date YYYY-MM-DD (default: today)"},
                    "cycle_end": {"type": "string", "description": "Cycle end date YYYY-MM-DD"},
                    "notes": {"type": "string", "description": "Additional notes about the protocol"}
                },
                "required": ["action", "peptide_name"]
            }
        },
        {
            "name": "log_peptide_dose",
            "description": "Record a peptide dose administration. Use when user says 'took my BPC', 'just pinned', 'did my dose', etc.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "peptide_name": {"type": "string", "description": "Name of the peptide"},
                    "dose_amount": {"type": "number", "description": "Amount administered (optional, uses protocol default)"},
                    "injection_site": {"type": "string", "description": "Where it was administered (e.g., 'abdomen', 'deltoid', 'glute')"},
                    "notes": {"type": "string", "description": "Any notes (side effects, how it felt)"}
                },
                "required": ["peptide_name"]
            }
        },
        {
            "name": "manage_supplement",
            "description": "Add or remove a supplement from the stack. Use when user mentions starting/stopping a supplement.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "remove"], "description": "Add or remove the supplement"},
                    "supplement_name": {"type": "string", "description": "Name of the supplement (e.g., 'Creatine', 'Vitamin D3', 'Magnesium')"},
                    "dose_amount": {"type": "number", "description": "Dose amount"},
                    "dose_unit": {"type": "string", "description": "Unit (mg, g, IU, mcg)"},
                    "frequency": {"type": "string", "description": "How often (daily, twice daily, etc.)"},
                    "timing": {"type": "string", "description": "When to take it (morning, evening, with meals, before bed, pre-workout, post-workout)"}
                },
                "required": ["action", "supplement_name"]
            }
        },
        {
            "name": "log_supplement_taken",
            "description": "Mark supplements as taken. Use when user says 'took my supplements', 'had my creatine', etc. Use supplement_name='all' to mark entire stack as taken.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "supplement_name": {"type": "string", "description": "Name of specific supplement, or 'all' for entire stack"}
                },
                "required": ["supplement_name"]
            }
        },
        {
            "name": "log_bloodwork",
            "description": "Log bloodwork results with individual biomarkers. Use when user shares lab results, blood test numbers, or specific biomarker values.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "test_date": {"type": "string", "description": "Date of the blood test YYYY-MM-DD"},
                    "lab_name": {"type": "string", "description": "Name of the lab (optional)"},
                    "markers": {
                        "type": "array",
                        "description": "Individual biomarkers from the panel",
                        "items": {
                            "type": "object",
                            "properties": {
                                "marker_name": {"type": "string", "description": "Biomarker name (e.g., 'Total Testosterone', 'Vitamin D', 'hsCRP')"},
                                "value": {"type": "number", "description": "The measured value"},
                                "unit": {"type": "string", "description": "Unit of measurement (ng/dL, ng/mL, mg/L, etc.)"},
                                "reference_low": {"type": "number", "description": "Low end of reference range"},
                                "reference_high": {"type": "number", "description": "High end of reference range"}
                            },
                            "required": ["marker_name", "value"]
                        }
                    },
                    "notes": {"type": "string", "description": "Notes about the panel"}
                },
                "required": ["test_date", "markers"]
            }
        },
        {
            "name": "get_biohacking_context",
            "description": "Get full biohacking summary: active peptide protocols with adherence, supplement stack, latest bloodwork with flagged markers. Call this before giving biohacking advice.",
            "input_schema": {"type": "object", "properties": {}}
        },
        # --- WHOOP tools ---
        {
            "name": "get_whoop_status",
            "description": "Get today's WHOOP recovery score, HRV, sleep performance, strain, and 7-day trends. Call this when user asks about recovery, readiness, how they should train, or anything WHOOP-related. Also syncs latest data from WHOOP.",
            "input_schema": {"type": "object", "properties": {}}
        },
        {
            "name": "connect_whoop",
            "description": "Generate WHOOP OAuth URL for user to link their WHOOP device. Use when user says 'connect WHOOP', 'link my WHOOP', etc.",
            "input_schema": {"type": "object", "properties": {}}
        },
        {
            "name": "get_whoop_insights",
            "description": "Get cross-domain intelligence: how recovery correlates with training strain, sleep quality, peptide dosing, and rest patterns over 30 days. Call when user asks 'do you see patterns?', 'how are peptides affecting my recovery?', 'am I overtraining?', or any question about long-term trends connecting WHOOP data to their training and protocols.",
            "input_schema": {"type": "object", "properties": {}}
        },
        # --- Memory tools ---
        {
            "name": "save_user_memory",
            "description": "Save a fact you learned about the user for future conversations. Use this PROACTIVELY whenever you learn something new: their name, job, goals, preferences, injuries, training style, schedule, likes/dislikes, personal context. This makes you smarter over time. Write memories as concise facts, not full sentences.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The fact to remember. Concise: 'prefers morning workouts' not 'The user told me they prefer to work out in the morning'"},
                    "category": {
                        "type": "string",
                        "enum": ["preference", "personal", "fitness", "health", "coaching", "goal", "general"],
                        "description": "Category: preference (likes/dislikes), personal (job, location, life), fitness (training facts), health (conditions, diet), coaching (how they like feedback), goal (what they're working toward), general (anything else)"
                    }
                },
                "required": ["content", "category"]
            }
        },
        {
            "name": "forget_user_memory",
            "description": "Delete a memory about the user. Use when they say something is no longer true, or ask you to forget something.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "content_match": {"type": "string", "description": "Substring to match against stored memories. All matching memories will be deleted."}
                },
                "required": ["content_match"]
            }
        },
        {
            "name": "search_knowledge_base",
            "description": "Search Zoe's health, fitness, and longevity knowledge base. Use when asked about a peptide, supplement interaction, biomarker interpretation, blood type foods, or expert protocols (Huberman, Attia, Sinclair, Lyon). This is your reference library.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for (e.g. 'BPC-157 dosing', 'foods beneficial for blood type O', 'sauna protocol', 'vitamin D optimal range')"
                    },
                    "search_type": {
                        "type": "string",
                        "enum": ["general", "peptide", "supplement", "biomarker", "food"],
                        "description": "Type of knowledge to search. 'general' searches expert protocols and research, others search specific reference tables."
                    },
                    "blood_type": {
                        "type": "string",
                        "enum": ["O", "A", "B", "AB"],
                        "description": "For food searches, filter by blood type."
                    }
                },
                "required": ["query"]
            }
        },
        {
            "name": "check_peptide_interactions",
            "description": "Check known interactions between peptides. Use when a user asks about combining peptides, stacking safety, or whether two compounds interact. Returns synergistic, contraindicated, or cautionary interactions.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "peptide_name": {
                        "type": "string",
                        "description": "Peptide name to check interactions for (e.g. 'BPC-157', 'Ipamorelin')"
                    },
                    "second_peptide": {
                        "type": "string",
                        "description": "Optional: check interaction with a specific second peptide"
                    }
                },
                "required": ["peptide_name"]
            }
        },
        {
            "name": "get_stacking_protocols",
            "description": "Get curated peptide stacking protocols for specific goals. Use when user asks about peptide stacks, protocol recommendations, combining compounds for a goal (recovery, GH optimization, cognition, longevity, fat loss, immune, gut healing).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "Goal to filter protocols by (e.g. 'recovery', 'fat loss', 'cognition', 'longevity'). Leave empty for all protocols."
                    },
                    "slug": {
                        "type": "string",
                        "description": "Get a specific protocol by slug (e.g. 'recovery-accelerator', 'gh-optimization')"
                    }
                }
            }
        },
        {
            "name": "get_regulatory_status",
            "description": "Get FDA and WADA regulatory status for a peptide. Use when user asks about legality, FDA status, is it banned, can they use it in competition, or regulatory concerns. Important for harm reduction.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "peptide_name": {
                        "type": "string",
                        "description": "Peptide name to check regulatory status for"
                    }
                },
                "required": ["peptide_name"]
            }
        },
        # --- Google Workspace tools ---
        {
            "name": "search_gmail",
            "description": "Search the user's Gmail inbox. Use when they ask about emails, messages from someone, unread mail, or want to find a specific email. Returns subject, sender, date, and snippet for each result.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Gmail search query. Examples: 'from:boss@company.com is:unread', 'subject:invoice newer_than:7d', 'is:unread'"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max emails to return (default: 5, max: 10)"
                    }
                },
                "required": ["query"]
            }
        },
        {
            "name": "send_email",
            "description": "Send an email via Gmail. Use when user asks to send, reply to, or draft an email. IMPORTANT: Always confirm recipient, subject, and body with the user before calling this tool unless they were completely explicit about all three.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string", "description": "Email subject line"},
                    "body": {"type": "string", "description": "Email body text (plain text)"}
                },
                "required": ["to", "subject", "body"]
            }
        },
        {
            "name": "search_drive",
            "description": "Search Google Drive for files and documents. Use when user asks to find a file, document, spreadsheet, or presentation. Returns file name, type, and shareable link.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term (file name or keyword). For advanced queries use Drive syntax: 'mimeType=\"application/pdf\"', 'modifiedTime > \"2024-01-01\"'"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max files to return (default: 5)"
                    }
                },
                "required": ["query"]
            }
        },
        {
            "name": "list_google_tasks",
            "description": "List tasks from Google Tasks (separate from Zoe's internal task system). Use when user specifically asks about their Google Tasks, or wants to see tasks from Google Calendar/Tasks app.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "show_completed": {
                        "type": "boolean",
                        "description": "Whether to include completed tasks (default: false)"
                    }
                }
            }
        },
        {
            "name": "add_google_task",
            "description": "Add a task to Google Tasks. Use ONLY when user explicitly wants to add to Google Tasks (not Zoe's internal tasks). Prefer add_task for Zoe's system unless user specifically says 'Google Tasks'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task title"},
                    "due_date": {"type": "string", "description": "Due date in YYYY-MM-DD format"},
                    "notes": {"type": "string", "description": "Task notes or description"}
                },
                "required": ["title"]
            }
        },
        {
            "name": "create_calendar_event",
            "description": "Create a new event on Google Calendar. Use when user asks to schedule something, add a meeting, block time, or create a calendar event.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Event title"},
                    "start_datetime": {"type": "string", "description": "Start in ISO format YYYY-MM-DDTHH:MM:SS"},
                    "end_datetime": {"type": "string", "description": "End in ISO format YYYY-MM-DDTHH:MM:SS. Default to 1 hour after start if not specified."},
                    "description": {"type": "string", "description": "Event description or notes"},
                    "location": {"type": "string", "description": "Event location"}
                },
                "required": ["summary", "start_datetime", "end_datetime"]
            }
        },
        {
            "name": "delete_calendar_event",
            "description": "Delete/cancel a Google Calendar event. Use when user asks to remove, cancel, or delete a calendar event. The event_id comes from the UPCOMING CALENDAR EVENTS list in context.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "The Google Calendar event ID from the events list"},
                    "event_title": {"type": "string", "description": "The event title (for confirmation)"}
                },
                "required": ["event_id"]
            }
        },
        {
            "name": "update_calendar_event",
            "description": "Update/reschedule a Google Calendar event. Use when user asks to move, reschedule, rename, or change a calendar event. Only provide fields that should change.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "The Google Calendar event ID from the events list"},
                    "summary": {"type": "string", "description": "New event title (if changing)"},
                    "start_datetime": {"type": "string", "description": "New start time in ISO format YYYY-MM-DDTHH:MM:SS (if rescheduling)"},
                    "end_datetime": {"type": "string", "description": "New end time in ISO format YYYY-MM-DDTHH:MM:SS (if rescheduling)"},
                    "description": {"type": "string", "description": "New event description (if changing)"},
                    "location": {"type": "string", "description": "New event location (if changing)"}
                },
                "required": ["event_id"]
            }
        },
        {
            "name": "list_calendar_events",
            "description": "List upcoming Google Calendar events. Use when user asks 'what's on my calendar?', 'read my calendar', 'what do I have today?', 'show my schedule'. Returns events for the next N days.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Number of days to look ahead (default 3, max 14)"}
                },
                "required": []
            }
        },
        {
            "name": "search_calendar_event",
            "description": "Search Google Calendar events by keyword. Use when user mentions a specific event by name (e.g., 'find the meeting with CEO', 'look for the 5K run'). Returns matching events with their IDs for update/delete operations.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query (event title or keywords)"},
                    "days": {"type": "integer", "description": "How many days ahead to search (default 14)"}
                },
                "required": ["query"]
            }
        },
        {
            "name": "get_remaining_messages",
            "description": "Check how many AI messages the user has used today and how many remain. Use when user asks 'how many messages do I have?', 'what's my limit?', 'how many messages left?', 'am I close to my limit?'.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "create_google_doc",
            "description": "Create a new Google Doc. Use when user asks to create a document, write something up, or start a new doc. Returns the editable document link.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Document title"},
                    "content": {"type": "string", "description": "Initial document content (optional)"}
                },
                "required": ["title"]
            }
        },
        # --- Habit tracking ---
        {
            "name": "add_habit",
            "description": "Create a new daily habit to track. Use when user wants to start tracking a habit like meditation, reading, cold plunge, journaling, etc.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Habit name (e.g. 'meditation', 'cold plunge', 'reading')"},
                    "frequency": {"type": "string", "enum": ["daily", "weekdays", "weekly"], "description": "How often. Default daily."}
                },
                "required": ["name"]
            }
        },
        {
            "name": "log_habit",
            "description": "Log a habit as completed for today. Use when user says they did a habit (e.g. 'I meditated', 'did my cold plunge', 'morning routine done'). Also use for checking off habits.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "habit_name": {"type": "string", "description": "Name of the habit to log (must match an existing habit)"}
                },
                "required": ["habit_name"]
            }
        },
        {
            "name": "get_habits",
            "description": "Get all tracked habits with today's completion status and streak info. Use when user asks about habits, streaks, or habit progress.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "include_summary": {"type": "boolean", "description": "Include 7-day and 30-day completion rates. Default false."}
                },
                "required": []
            }
        },
        # --- Expense tracking ---
        {
            "name": "log_expense",
            "description": "Log an expense. Use when user mentions spending money (e.g. 'spent €45 on groceries', 'paid €120 for electricity'). Infer category from context.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "Amount spent"},
                    "currency": {"type": "string", "description": "Currency code (EUR, USD, GBP). Default EUR."},
                    "category": {"type": "string", "enum": ["food", "transport", "utilities", "health", "shopping", "entertainment", "subscriptions", "dining", "travel", "education", "other"], "description": "Expense category — infer from context"},
                    "description": {"type": "string", "description": "What the expense was for"},
                    "expense_date": {"type": "string", "description": "Date in YYYY-MM-DD format. Default today."}
                },
                "required": ["amount", "description"]
            }
        },
        {
            "name": "get_expenses",
            "description": "Get recent expenses. Use when user asks what they spent, expense history, recent purchases.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Number of days to look back. Default 30."}
                },
                "required": []
            }
        },
        {
            "name": "get_spending_summary",
            "description": "Get spending summary by category. Use when user asks for spending breakdown, totals by category, or monthly spending.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Number of days to look back. Default 30."}
                },
                "required": []
            }
        },
        # --- URL recall ---
        {
            "name": "recall_saved_url",
            "description": "Search previously saved URL summaries. Use when user asks about a link they sent before (e.g. 'what was that article about creatine?', 'find that link I sent').",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keyword(s) to find in saved URL titles, summaries, or domains"}
                },
                "required": ["query"]
            }
        },
        # --- Nutrition tools ---
        {
            "name": "update_nutrition_profile",
            "description": "Set or update user's nutrition profile: calorie targets, macro targets, dietary restrictions, meals per day. Use when user mentions diet, calories, macros, eating patterns, vegan/keto/gluten-free, or meal frequency.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "daily_calorie_target": {"type": "integer", "description": "Daily calorie target (e.g. 2500)"},
                    "protein_target_g": {"type": "integer", "description": "Daily protein target in grams"},
                    "carbs_target_g": {"type": "integer", "description": "Daily carb target in grams"},
                    "fat_target_g": {"type": "integer", "description": "Daily fat target in grams"},
                    "dietary_restrictions": {"type": "array", "items": {"type": "string"}, "description": "Dietary restrictions (e.g. ['vegan', 'gluten_free', 'keto', 'dairy_free', 'low_carb'])"},
                    "meals_per_day": {"type": "integer", "description": "How many meals per day (default 3)"}
                }
            }
        },
        {
            "name": "log_meal",
            "description": "Log a meal with nutrition data. For food photos, USDA-verified data is provided — use it with source='usda'. For text descriptions, estimate and use source='ai_estimated'. Always log even rough estimates.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "meal_type": {"type": "string", "enum": ["breakfast", "lunch", "dinner", "snack"], "description": "Meal type. Infer from time of day if user doesn't specify."},
                    "description": {"type": "string", "description": "What they ate (e.g. 'grilled chicken breast with rice and broccoli')"},
                    "calories": {"type": "integer", "description": "Total calories"},
                    "protein_g": {"type": "number", "description": "Protein in grams"},
                    "carbs_g": {"type": "number", "description": "Carbs in grams"},
                    "fat_g": {"type": "number", "description": "Fat in grams"},
                    "fiber_g": {"type": "number", "description": "Fiber in grams"},
                    "vitamin_d_mcg": {"type": "number", "description": "Vitamin D in micrograms"},
                    "magnesium_mg": {"type": "number", "description": "Magnesium in mg"},
                    "zinc_mg": {"type": "number", "description": "Zinc in mg"},
                    "iron_mg": {"type": "number", "description": "Iron in mg"},
                    "b12_mcg": {"type": "number", "description": "Vitamin B12 in micrograms"},
                    "potassium_mg": {"type": "number", "description": "Potassium in mg"},
                    "vitamin_c_mg": {"type": "number", "description": "Vitamin C in mg"},
                    "calcium_mg": {"type": "number", "description": "Calcium in mg"},
                    "sodium_mg": {"type": "number", "description": "Sodium in mg"},
                    "source": {"type": "string", "enum": ["usda", "ai_estimated", "manual"], "description": "Data source. 'usda' for USDA-verified photo data, 'ai_estimated' for text-based estimates, 'manual' for user-entered."}
                },
                "required": ["description", "calories"]
            }
        },
        {
            "name": "get_daily_nutrition",
            "description": "Get today's food intake summary: meals logged, total calories, macros, and remaining vs target. Use when user asks 'what have I eaten today', 'how many calories', 'am I on track', 'calories left', etc.",
            "input_schema": {
                "type": "object",
                "properties": {}
            }
        },
        # --- Pain / Mobility tools ---
        {
            "name": "report_pain",
            "description": "Log a pain report when user mentions pain, discomfort, tightness, or injury. Returns upstream/downstream analysis and specific mobility prescription. Use when user says 'my knee hurts', 'back is tight', 'shoulder pain', etc.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "Body area: knee, low_back, shoulder, neck, hip, ankle, elbow, wrist, thoracic_spine, foot"},
                    "severity": {"type": "integer", "description": "Pain severity 1-10 (1=mild discomfort, 10=worst pain)"},
                    "pain_type": {"type": "string", "enum": ["sharp", "dull", "aching", "burning", "stiffness", "tightness", "shooting", "throbbing"], "description": "Type of pain"},
                    "triggers": {"type": "string", "description": "What triggers or worsens it (e.g. 'squatting', 'sitting for long time', 'overhead press')"},
                    "description": {"type": "string", "description": "User's description of the pain"},
                    "onset": {"type": "string", "enum": ["acute", "gradual", "chronic"], "description": "How it started: acute (sudden), gradual (over days/weeks), chronic (months+)"}
                },
                "required": ["location", "severity"]
            }
        },
        {
            "name": "get_pain_history",
            "description": "Get user's pain history and active issues. Use when asking about previous injuries, pain patterns, or before programming a workout for someone with known issues.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["active", "resolved", "all"], "description": "Filter by status (default: active)"}
                }
            }
        },
        {
            "name": "resolve_pain",
            "description": "Mark a pain report as resolved. Use when user says pain is gone, feeling better, no more issues.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pain_id": {"type": "integer", "description": "ID of the pain report to resolve"},
                    "notes": {"type": "string", "description": "Resolution notes (what helped, how long it took)"}
                },
                "required": ["pain_id"]
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
            user_is_admin = False
            u = None
            try:
                from bot.services import user_service
                u = user_service.get_user_by_id(user_id)
                user_tier = u.get("tier", "free") if u else "free"
                user_is_admin = u.get("is_admin", False) if u else False
            except Exception:
                pass
            user_tg_id = u.get("telegram_user_id") if u else None
            allowed, limit_msg = check_limit(user_id, "set_reminder", user_tier, is_admin=user_is_admin, telegram_user_id=user_tg_id)
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

        # --- Interactive workout session ---
        elif name == "start_workout_session":
            from bot.services import fitness_service
            logger.info(f"WORKOUT CARDS: start_workout_session called for user {user_id}, title={args['title']}, {len(args['exercises'])} exercises")
            session = fitness_service.create_workout_session(
                user_id=user_id,
                title=args["title"],
                exercises=args["exercises"],
            )
            logger.info(f"WORKOUT CARDS: session created id={session['id']}, exercises={len(session.get('exercises', []))}")
            return {
                "success": True,
                "session_id": session["id"],
                "title": args["title"],
                "exercise_count": len(args["exercises"]),
                "_interactive_session": True,
            }

        # --- Interactive protocol wizard & dashboard ---
        elif name == "start_protocol_wizard":
            peptide_hint = args.get("peptide_hint")
            return {
                "success": True,
                "message": "Protocol wizard launched — the user will see an interactive card to set up their protocol.",
                "_interactive_protocol_wizard": True,
                "_peptide_hint": peptide_hint,
            }

        elif name == "get_protocol_dashboard":
            from bot.services import biohacking_service
            protocols = biohacking_service.get_active_protocols(user_id)
            if not protocols:
                return {"protocols": [], "message": "No active protocols. The user can start one with the protocol wizard."}
            return {
                "success": True,
                "protocol_count": len(protocols),
                "message": "Dashboard card sent — the user can see progress, log doses, and manage protocols interactively.",
                "_interactive_protocol_dashboard": True,
            }

        # --- Biohacking tools ---
        elif name == "manage_peptide_protocol":
            from bot.services import biohacking_service
            action = args["action"]
            peptide_name = args["peptide_name"]

            if action == "add":
                cycle_start = None
                cycle_end = None
                if args.get("cycle_start"):
                    try:
                        cycle_start = datetime.fromisoformat(args["cycle_start"]).date()
                    except (ValueError, TypeError):
                        cycle_start = datetime.now().date()
                if args.get("cycle_end"):
                    try:
                        cycle_end = datetime.fromisoformat(args["cycle_end"]).date()
                    except (ValueError, TypeError):
                        pass
                protocol = biohacking_service.add_protocol(
                    user_id=user_id,
                    peptide_name=peptide_name,
                    dose_amount=args.get("dose_amount"),
                    dose_unit=args.get("dose_unit", "mcg"),
                    frequency=args.get("frequency"),
                    route=args.get("route", "subcutaneous"),
                    cycle_start=cycle_start,
                    cycle_end=cycle_end,
                    notes=args.get("notes"),
                )
                result = {
                    "success": True,
                    "action": "added",
                    "protocol_id": protocol["id"],
                    "peptide": peptide_name,
                }
                if cycle_start and cycle_end:
                    total = (cycle_end - cycle_start).days
                    result["cycle_length_days"] = total
                return result

            elif action in ("pause", "resume", "end"):
                protocol = biohacking_service.get_protocol_by_name(user_id, peptide_name)
                if not protocol:
                    return {"error": f"No active protocol found for {peptide_name}"}
                new_status = {"pause": "paused", "resume": "active", "end": "completed"}[action]
                biohacking_service.update_protocol_status(protocol["id"], new_status)
                return {"success": True, "action": action, "peptide": peptide_name, "new_status": new_status}

        elif name == "log_peptide_dose":
            from bot.services import biohacking_service
            peptide_name = args["peptide_name"]
            protocol = biohacking_service.get_protocol_by_name(user_id, peptide_name)
            if not protocol:
                return {"error": f"No active protocol found for '{peptide_name}'. Add one first with manage_peptide_protocol."}
            dose = biohacking_service.log_dose(
                user_id=user_id,
                protocol_id=protocol["id"],
                dose_amount=args.get("dose_amount") or protocol.get("dose_amount"),
                site=args.get("injection_site"),
                notes=args.get("notes"),
            )
            # Get cycle progress
            result = {
                "success": True,
                "peptide": peptide_name,
                "dose_amount": dose.get("dose_amount"),
                "dose_unit": protocol.get("dose_unit", "mcg"),
            }
            if protocol.get("cycle_start") and protocol.get("cycle_end"):
                today = datetime.now().date()
                elapsed = (today - protocol["cycle_start"]).days
                total = (protocol["cycle_end"] - protocol["cycle_start"]).days
                result["cycle_day"] = elapsed
                result["cycle_total"] = total
                result["days_remaining"] = max(0, (protocol["cycle_end"] - today).days)
            # Recent dose count
            doses = biohacking_service.get_dose_history(user_id, protocol["id"], days=7)
            result["doses_last_7d"] = len(doses)
            return result

        elif name == "manage_supplement":
            from bot.services import biohacking_service
            action = args["action"]
            supp_name = args["supplement_name"]

            if action == "add":
                supp = biohacking_service.add_supplement(
                    user_id=user_id,
                    supplement_name=supp_name,
                    dose_amount=args.get("dose_amount"),
                    dose_unit=args.get("dose_unit"),
                    frequency=args.get("frequency", "daily"),
                    timing=args.get("timing"),
                )
                return {
                    "success": True,
                    "action": "added",
                    "supplement": supp_name,
                    "supplement_id": supp["id"],
                }
            elif action == "remove":
                supp = biohacking_service.get_supplement_by_name(user_id, supp_name)
                if not supp:
                    return {"error": f"No active supplement found for '{supp_name}'"}
                biohacking_service.update_supplement_status(supp["id"], "removed")
                return {"success": True, "action": "removed", "supplement": supp_name}

        elif name == "log_supplement_taken":
            from bot.services import biohacking_service
            supp_name = args["supplement_name"]

            if supp_name.lower() == "all":
                logged = biohacking_service.log_all_supplements_taken(user_id)
                return {"success": True, "logged": logged, "count": len(logged)}
            else:
                supp = biohacking_service.get_supplement_by_name(user_id, supp_name)
                if not supp:
                    return {"error": f"No active supplement found for '{supp_name}'. Add it first."}
                biohacking_service.log_supplement_taken(user_id, supp["id"])
                return {"success": True, "supplement": supp_name}

        elif name == "log_bloodwork":
            from bot.services import biohacking_service
            try:
                test_date = datetime.fromisoformat(args["test_date"]).date()
            except (ValueError, TypeError):
                test_date = datetime.now().date()
            panel = biohacking_service.log_bloodwork(
                user_id=user_id,
                test_date=test_date,
                lab_name=args.get("lab_name"),
                notes=args.get("notes"),
                markers=args.get("markers", []),
            )
            # Get flagged markers
            flagged = biohacking_service.get_flagged_biomarkers(user_id)
            result = {
                "success": True,
                "panel_id": panel["id"],
                "test_date": test_date.isoformat(),
                "marker_count": panel["marker_count"],
            }
            if flagged:
                result["flagged_markers"] = [
                    {"marker": f["marker_name"], "value": f["value"], "unit": f.get("unit"), "flag": f["flag"]}
                    for f in flagged
                ]
            return result

        elif name == "get_biohacking_context":
            from bot.services import biohacking_service
            summary = biohacking_service.get_biohacking_summary(user_id)
            result = {}

            # Protocols
            protocols = []
            for p in summary["protocols"]:
                entry = {
                    "peptide": p["peptide_name"],
                    "dose": f"{p.get('dose_amount')} {p.get('dose_unit', 'mcg')}",
                    "frequency": p.get("frequency"),
                    "route": p.get("route"),
                    "status": p.get("status"),
                }
                if p.get("cycle_day") is not None:
                    entry["cycle_progress"] = f"Day {p['cycle_day']} of {p['cycle_total']}"
                    entry["days_remaining"] = p["days_remaining"]
                entry["doses_last_7d"] = p.get("doses_last_7d", 0)
                protocols.append(entry)
            result["active_protocols"] = protocols

            # Supplements
            supps = []
            for s in summary["supplements"]:
                entry = {"name": s["supplement_name"]}
                if s.get("dose_amount") and s.get("dose_unit"):
                    entry["dose"] = f"{s['dose_amount']}{s['dose_unit']}"
                if s.get("timing"):
                    entry["timing"] = s["timing"]
                supps.append(entry)
            result["supplement_stack"] = supps
            result["supplement_adherence_7d"] = summary["supplement_adherence"]["overall_rate"]

            # Bloodwork
            bw = summary["latest_bloodwork"]
            if bw:
                result["latest_bloodwork"] = {
                    "date": bw["test_date"].isoformat() if bw.get("test_date") else None,
                    "lab": bw.get("lab_name"),
                    "markers": [
                        {
                            "name": m["marker_name"],
                            "value": m["value"],
                            "unit": m.get("unit"),
                            "flag": m.get("flag"),
                        }
                        for m in bw.get("markers", [])
                    ],
                }
            result["flagged_biomarkers"] = [
                {"marker": f["marker_name"], "value": f["value"], "unit": f.get("unit"), "flag": f["flag"]}
                for f in summary["flagged_biomarkers"]
            ]
            return result

        # --- WHOOP tools ---
        elif name == "get_whoop_status":
            from bot.services import whoop_service
            if not whoop_service.is_connected(user_id):
                return {"error": "WHOOP not connected. Use connect_whoop to link your device."}
            # Sync fresh data
            try:
                whoop_service.sync_all(user_id)
            except Exception:
                pass
            today = whoop_service.get_today_recovery(user_id)
            trends = whoop_service.get_whoop_trends(user_id, days=7)
            result = {"connected": True}
            if today:
                zone = whoop_service.get_recovery_zone(today.get("recovery_score"))
                result["today"] = {
                    "recovery_score": today.get("recovery_score"),
                    "recovery_zone": zone,
                    "hrv_rmssd": today.get("hrv_rmssd"),
                    "resting_hr": today.get("resting_hr"),
                    "spo2": today.get("spo2"),
                    "skin_temp": today.get("skin_temp"),
                    "sleep_performance": today.get("sleep_performance"),
                    "deep_sleep_min": today.get("deep_sleep_minutes"),
                    "rem_sleep_min": today.get("rem_sleep_minutes"),
                    "daily_strain": today.get("daily_strain"),
                }
            if trends and trends.get("days", 0) > 0:
                result["trends_7d"] = {
                    "recovery_avg": trends.get("recovery_avg"),
                    "recovery_trend": trends.get("recovery_trend"),
                    "hrv_avg": trends.get("hrv_avg"),
                    "hrv_trend": trends.get("hrv_trend"),
                    "rhr_avg": trends.get("rhr_avg"),
                    "sleep_avg": trends.get("sleep_avg"),
                    "strain_avg": trends.get("strain_avg"),
                }
            return result

        elif name == "connect_whoop":
            from bot.services import whoop_service
            if not whoop_service.is_configured():
                return {"error": "WHOOP integration is not configured yet. Coming soon!"}
            if whoop_service.is_connected(user_id):
                return {"already_connected": True, "message": "WHOOP is already linked. Use get_whoop_status to see your data."}
            url = whoop_service.get_auth_url(user_id)
            if url:
                return {"auth_url": url, "message": "Click the link to connect your WHOOP account."}
            return {"error": "Could not generate WHOOP authorization URL."}

        elif name == "get_whoop_insights":
            from bot.services import whoop_service
            if not whoop_service.is_connected(user_id):
                return {"error": "WHOOP not connected. Use connect_whoop to link your device."}
            try:
                whoop_service.sync_all(user_id)
            except Exception:
                pass
            insights = whoop_service.get_whoop_insights(user_id)
            if insights:
                return {"insights": insights, "count": len(insights)}
            return {"insights": [], "message": "Not enough data yet — need 7-10+ days of WHOOP + training data to find patterns."}

        # --- Memory tools ---
        elif name == "save_user_memory":
            from bot.services import memory_service
            result = memory_service.save_memory(
                user_id=user_id,
                content=args["content"],
                category=args.get("category", "general"),
            )
            return {"success": True, **result}

        elif name == "forget_user_memory":
            from bot.services import memory_service
            count = memory_service.forget_by_content(user_id, args["content_match"])
            if count > 0:
                return {"success": True, "deleted": count}
            return {"success": False, "message": "No matching memories found."}

        # --- Knowledge base ---
        elif name == "search_knowledge_base":
            from bot.services import knowledge_service
            query = args["query"]
            search_type = args.get("search_type", "general")
            blood_type = args.get("blood_type")
            # Auto-populate blood type from user profile if not specified
            if not blood_type and search_type == "food":
                from bot.services import user_service
                u = user_service.get_user_by_id(user_id)
                if u and u.get("blood_type"):
                    blood_type = u["blood_type"]

            if search_type == "peptide":
                info = knowledge_service.get_peptide_info(query)
                if info:
                    result = {
                        "name": info["name"], "description": info["description"],
                        "mechanism": info.get("mechanism"),
                        "dose": info.get("standard_dose"),
                        "frequency": info.get("standard_frequency"),
                        "duration": info.get("standard_duration"),
                        "dosage_notes": info.get("dosage_notes"),
                        "benefits": info.get("benefits"),
                        "routes": info.get("routes"),
                        "side_effects": info.get("side_effects"),
                        "contraindications": info.get("contraindications"),
                        "stack_suggestions": info.get("stack_suggestions"),
                        "evidence_level": info.get("evidence_level"),
                        "research_summary": info.get("research_summary"),
                        "half_life": info.get("half_life"),
                    }
                    # Include regulatory data if available
                    if info.get("fda_status"):
                        result["fda_status"] = info["fda_status"]
                    if info.get("wada_prohibited") is not None:
                        result["wada_prohibited"] = info["wada_prohibited"]
                        result["wada_category"] = info.get("wada_category")
                    if info.get("legal_notes"):
                        result["legal_notes"] = info["legal_notes"]
                    return {"type": "peptide", "result": result}
                results = knowledge_service.search_peptides(query)
                return {"type": "peptide_search", "results": results, "count": len(results)}

            elif search_type == "biomarker":
                info = knowledge_service.get_biomarker_info(query)
                if info:
                    return {"type": "biomarker", "result": {
                        "marker": info["marker_name"], "unit": info["unit"],
                        "category": info["category"],
                        "lab_range": f"{info.get('lab_range_low')}-{info.get('lab_range_high')}",
                        "optimal_range": f"{info.get('optimal_range_low')}-{info.get('optimal_range_high')}",
                        "interpretation_low": info.get("interpretation_low"),
                        "interpretation_high": info.get("interpretation_high"),
                        "tips": info.get("optimization_tips"),
                        "related": info.get("related_markers"),
                    }}
                return {"type": "biomarker", "result": None, "message": f"No reference data for '{query}'"}

            elif search_type == "food":
                if blood_type:
                    results = knowledge_service.get_foods_by_blood_type(
                        blood_type, query=query if query.lower() not in ("all", "list", "foods") else None
                    )
                else:
                    results = knowledge_service.search_foods(query)
                return {"type": "food", "results": results[:15], "count": len(results)}

            elif search_type == "supplement":
                info = knowledge_service.get_supplement_info(query)
                if info:
                    return {"type": "supplement", "result": {
                        "name": info["name"], "category": info["category"],
                        "description": info["description"],
                        "dose": info["standard_dose"], "timing": info.get("timing"),
                        "benefits": info.get("benefits"),
                        "mechanism": info.get("mechanism"),
                        "interactions": info.get("interactions"),
                        "side_effects": info.get("side_effects"),
                        "cycle": info.get("cycle_recommendation"),
                        "evidence_level": info.get("evidence_level"),
                        "notes": info.get("notes"),
                    }}
                results = knowledge_service.search_supplements(query)
                return {"type": "supplement_search", "results": results, "count": len(results)}

            else:  # general
                results = knowledge_service.search_kb(query, limit=5)
                return {"type": "knowledge", "results": results, "count": len(results)}

        elif name == "check_peptide_interactions":
            from bot.services import knowledge_service
            peptide_name = args["peptide_name"]
            second = args.get("second_peptide")

            if second:
                pair = knowledge_service.check_interaction_pair(peptide_name, second)
                if pair:
                    return {"type": "interaction_pair", "result": pair}
                return {"type": "interaction_pair", "result": None,
                        "message": f"No known interaction between {peptide_name} and {second}"}
            else:
                interactions = knowledge_service.check_peptide_interactions(peptide_name)
                return {"type": "interactions", "peptide": peptide_name,
                        "results": interactions, "count": len(interactions)}

        elif name == "get_stacking_protocols":
            from bot.services import knowledge_service
            slug = args.get("slug")
            goal = args.get("goal")

            if slug:
                protocol = knowledge_service.get_stacking_protocol_by_slug(slug)
                if protocol:
                    return {"type": "stacking_protocol", "result": protocol}
                return {"type": "stacking_protocol", "result": None,
                        "message": f"No protocol found with slug '{slug}'"}
            else:
                protocols = knowledge_service.get_stacking_protocols(goal)
                return {"type": "stacking_protocols", "results": protocols, "count": len(protocols)}

        elif name == "get_regulatory_status":
            from bot.services import knowledge_service
            peptide_name = args["peptide_name"]
            status = knowledge_service.get_regulatory_status(peptide_name)
            if status:
                return {"type": "regulatory", "result": status}
            return {"type": "regulatory", "result": None,
                    "message": f"No regulatory data for '{peptide_name}'"}

        # --- Google Workspace tools ---
        elif name == "search_gmail":
            from bot.services.google_auth import is_connected, has_scopes
            from bot.services import google_workspace
            if not is_connected(user_id):
                return {"error": "Google not connected. Tell the user to use /google to connect."}
            if not has_scopes(user_id, ["https://www.googleapis.com/auth/gmail.readonly"]):
                return {"error": "Gmail access not granted. Tell the user to use /google to connect with full permissions."}
            results = google_workspace.search_gmail(
                user_id,
                query=args["query"],
                max_results=min(args.get("max_results", 5), 10),
            )
            if not results:
                return {"emails": [], "message": "No emails found matching that search."}
            return {"emails": results, "count": len(results)}

        elif name == "send_email":
            from bot.services.google_auth import is_connected, has_scopes
            from bot.services import google_workspace
            if not is_connected(user_id):
                return {"error": "Google not connected. Tell the user to use /google to connect."}
            if not has_scopes(user_id, ["https://www.googleapis.com/auth/gmail.send"]):
                return {"error": "Gmail send permission not granted. Tell the user to use /google to reconnect with full permissions."}
            success = google_workspace.send_email(
                user_id,
                to=args["to"],
                subject=args["subject"],
                body=args["body"],
            )
            if success:
                return {"success": True, "to": args["to"], "subject": args["subject"]}
            return {"error": "Failed to send email. Check the recipient address and try again."}

        elif name == "search_drive":
            from bot.services.google_auth import is_connected, has_scopes
            from bot.services import google_workspace
            if not is_connected(user_id):
                return {"error": "Google not connected. Tell the user to use /google to connect."}
            if not has_scopes(user_id, ["https://www.googleapis.com/auth/drive.readonly"]):
                return {"error": "Drive access not granted. Tell the user to use /google to connect with full permissions."}
            files = google_workspace.search_drive(
                user_id,
                query=args["query"],
                max_results=args.get("max_results", 5),
            )
            if not files:
                return {"files": [], "message": "No files found matching that search."}
            return {"files": files, "count": len(files)}

        elif name == "list_google_tasks":
            from bot.services.google_auth import is_connected, has_scopes
            from bot.services import google_workspace
            if not is_connected(user_id):
                return {"error": "Google not connected. Tell the user to use /google to connect."}
            if not has_scopes(user_id, ["https://www.googleapis.com/auth/tasks"]):
                return {"error": "Google Tasks access not granted. Tell the user to use /google to connect."}
            tasks = google_workspace.list_google_tasks(
                user_id,
                show_completed=args.get("show_completed", False),
            )
            return {"tasks": tasks, "count": len(tasks)}

        elif name == "add_google_task":
            from bot.services.google_auth import is_connected, has_scopes
            from bot.services import google_workspace
            if not is_connected(user_id):
                return {"error": "Google not connected. Tell the user to use /google to connect."}
            if not has_scopes(user_id, ["https://www.googleapis.com/auth/tasks"]):
                return {"error": "Google Tasks access not granted. Tell the user to use /google to connect."}
            due = None
            if args.get("due_date"):
                try:
                    d = datetime.fromisoformat(args["due_date"])
                    due = d.strftime("%Y-%m-%dT09:00:00.000Z")
                except (ValueError, TypeError):
                    pass
            task = google_workspace.add_google_task(
                user_id,
                title=args["title"],
                due_date=due,
                notes=args.get("notes"),
            )
            if task:
                return {"success": True, "title": args["title"], "task_id": task.get("id")}
            return {"error": "Failed to add task to Google Tasks."}

        elif name == "list_calendar_events":
            from bot.services.google_auth import is_connected
            from bot.services import calendar_service
            if not is_connected(user_id):
                return {"error": "Google not connected. Tell the user to use /google to connect."}
            days = min(args.get("days", 3), 14)
            events = calendar_service.fetch_upcoming_events(user_id, days=days)
            if not events:
                return {"events": [], "message": f"No events in the next {days} days."}
            result = []
            for e in events:
                dt = e["start"]
                if e.get("all_day"):
                    time_str = dt.strftime("%A %b %d") + " (all day)"
                else:
                    time_str = dt.strftime("%A %b %d at %I:%M %p")
                result.append({
                    "title": e["title"],
                    "time": time_str,
                    "event_id": e.get("id", ""),
                })
            return {"events": result, "count": len(result)}

        elif name == "search_calendar_event":
            from bot.services.google_auth import is_connected
            from bot.services import calendar_service
            if not is_connected(user_id):
                return {"error": "Google not connected. Tell the user to use /google to connect."}
            query = args["query"]
            days = min(args.get("days", 14), 30)
            events = calendar_service.search_events(user_id, query, days=days)
            if not events:
                return {"events": [], "message": f"No events matching '{query}' in the next {days} days."}
            result = []
            for e in events:
                dt = e["start"]
                if e.get("all_day"):
                    time_str = dt.strftime("%A %b %d") + " (all day)"
                else:
                    time_str = dt.strftime("%A %b %d at %I:%M %p")
                result.append({
                    "title": e["title"],
                    "time": time_str,
                    "event_id": e.get("id", ""),
                })
            return {"events": result, "count": len(result)}

        elif name == "get_remaining_messages":
            from bot.services import tier_service
            tier = user.get("tier", "free")
            used = tier_service.get_usage_today(user_id, "ai_message")
            limits = tier_service.LIMITS.get(tier, tier_service.LIMITS["free"])
            max_msgs = limits["max_ai_messages_per_day"]
            if max_msgs is None:
                return {"tier": tier, "used_today": used, "remaining": "unlimited", "limit": "unlimited"}
            return {"tier": tier, "used_today": used, "remaining": max(0, max_msgs - used), "limit": max_msgs}

        elif name == "create_calendar_event":
            from bot.services.google_auth import is_connected, has_scopes
            from bot.services import calendar_service
            if not is_connected(user_id):
                return {"error": "Google not connected. Tell the user to use /google to connect."}
            if not has_scopes(user_id, ["https://www.googleapis.com/auth/calendar"]):
                return {"error": "Calendar write permission not granted. Tell the user to use /google to reconnect with full permissions."}
            try:
                start = datetime.fromisoformat(args["start_datetime"])
                end = datetime.fromisoformat(args["end_datetime"])
            except (ValueError, TypeError):
                return {"error": "Invalid datetime format. Use YYYY-MM-DDTHH:MM:SS"}
            event = calendar_service.create_event(
                user_id=user_id,
                summary=args["summary"],
                start_dt=start,
                end_dt=end,
                description=args.get("description"),
                location=args.get("location"),
            )
            if event:
                return {
                    "success": True,
                    "event": args["summary"],
                    "start": args["start_datetime"],
                    "link": event.get("htmlLink", ""),
                }
            return {"error": "Failed to create calendar event."}

        elif name == "delete_calendar_event":
            from bot.services.google_auth import is_connected, has_scopes
            from bot.services import calendar_service
            if not is_connected(user_id):
                return {"error": "Google not connected. Tell the user to use /google to connect."}
            if not has_scopes(user_id, ["https://www.googleapis.com/auth/calendar"]):
                return {"error": "Calendar write permission not granted. Tell the user to use /google to reconnect with full permissions."}
            success = calendar_service.delete_event(user_id, args["event_id"])
            if success:
                return {"success": True, "deleted": args.get("event_title", args["event_id"])}
            return {"error": "Failed to delete calendar event. The event may have already been removed."}

        elif name == "update_calendar_event":
            from bot.services.google_auth import is_connected, has_scopes
            from bot.services import calendar_service
            if not is_connected(user_id):
                return {"error": "Google not connected. Tell the user to use /google to connect."}
            if not has_scopes(user_id, ["https://www.googleapis.com/auth/calendar"]):
                return {"error": "Calendar write permission not granted. Tell the user to use /google to reconnect with full permissions."}
            start = None
            end = None
            if args.get("start_datetime"):
                try:
                    start = datetime.fromisoformat(args["start_datetime"])
                except (ValueError, TypeError):
                    return {"error": "Invalid start_datetime format. Use YYYY-MM-DDTHH:MM:SS"}
            if args.get("end_datetime"):
                try:
                    end = datetime.fromisoformat(args["end_datetime"])
                except (ValueError, TypeError):
                    return {"error": "Invalid end_datetime format. Use YYYY-MM-DDTHH:MM:SS"}
            event = calendar_service.update_event(
                user_id=user_id,
                event_id=args["event_id"],
                summary=args.get("summary"),
                start_dt=start,
                end_dt=end,
                description=args.get("description"),
                location=args.get("location"),
            )
            if event:
                return {"success": True, "updated": event.get("summary", args["event_id"])}
            return {"error": "Failed to update calendar event."}

        elif name == "create_google_doc":
            from bot.services.google_auth import is_connected, has_scopes
            from bot.services import google_workspace
            if not is_connected(user_id):
                return {"error": "Google not connected. Tell the user to use /google to connect."}
            if not has_scopes(user_id, ["https://www.googleapis.com/auth/documents"]):
                return {"error": "Google Docs access not granted. Tell the user to use /google to connect."}
            doc = google_workspace.create_google_doc(
                user_id,
                title=args["title"],
                content=args.get("content"),
            )
            if doc:
                return {
                    "success": True,
                    "title": args["title"],
                    "link": doc["link"],
                    "doc_id": doc["id"],
                }
            return {"error": "Failed to create document."}

        # --- Habit tracking executors ---

        elif name == "add_habit":
            from bot.services import habit_service
            habit = habit_service.add_habit(
                user_id, args["name"], args.get("frequency", "daily")
            )
            if habit:
                return {"success": True, "habit": habit["name"], "frequency": habit["frequency"]}
            return {"error": f"Habit '{args['name']}' already exists or couldn't be created."}

        elif name == "log_habit":
            from bot.services import habit_service
            result = habit_service.log_habit(user_id, args["habit_name"])
            if result is None:
                return {"error": f"No active habit matching '{args['habit_name']}'. Check /habits or tell the user to add it first."}
            return result

        elif name == "get_habits":
            from bot.services import habit_service
            if args.get("include_summary"):
                summary = habit_service.get_habit_summary(user_id)
                return summary
            habits = habit_service.get_habits(user_id)
            if not habits:
                return {"habits": [], "message": "No habits tracked yet."}
            return {
                "habits": [
                    {
                        "name": h["name"],
                        "done_today": h.get("done_today", False),
                        "current_streak": h.get("current_streak", 0),
                        "longest_streak": h.get("longest_streak", 0),
                    }
                    for h in habits
                ],
                "done_today": sum(1 for h in habits if h.get("done_today")),
                "total": len(habits),
            }

        # --- Expense tracking executors ---

        elif name == "log_expense":
            from bot.services import expense_service
            expense_date = None
            if args.get("expense_date"):
                try:
                    expense_date = datetime.fromisoformat(args["expense_date"]).date()
                except (ValueError, TypeError):
                    pass
            expense = expense_service.log_expense(
                user_id,
                amount=args["amount"],
                currency=args.get("currency", "EUR"),
                category=args.get("category", "other"),
                description=args["description"],
                expense_date=expense_date,
            )
            if expense:
                return {"success": True, "amount": args["amount"], "currency": args.get("currency", "EUR"), "category": args.get("category", "other"), "description": args["description"]}
            return {"error": "Failed to log expense."}

        elif name == "get_expenses":
            from bot.services import expense_service
            expenses = expense_service.get_expenses(user_id, days=args.get("days", 30))
            if not expenses:
                return {"expenses": [], "message": "No expenses found."}
            return {"expenses": expenses, "count": len(expenses)}

        elif name == "get_spending_summary":
            from bot.services import expense_service
            summary = expense_service.get_spending_summary(user_id, days=args.get("days", 30))
            return summary

        # --- URL recall executor ---

        elif name == "recall_saved_url":
            from bot.services import url_summarizer
            results = url_summarizer.search_saved_urls(user_id, args["query"])
            if not results:
                return {"results": [], "message": "No saved URLs matching that query."}
            return {"results": results, "count": len(results)}

        # --- Nutrition tools ---
        elif name == "update_nutrition_profile":
            from bot.services import nutrition_service
            profile = nutrition_service.update_nutrition_profile(user_id, **args)
            return {
                "status": "updated",
                "calorie_target": profile.get("daily_calorie_target"),
                "protein_target": profile.get("protein_target_g"),
                "carbs_target": profile.get("carbs_target_g"),
                "fat_target": profile.get("fat_target_g"),
                "dietary_restrictions": profile.get("dietary_restrictions"),
                "meals_per_day": profile.get("meals_per_day"),
            }

        elif name == "log_meal":
            from bot.services import nutrition_service
            meal = nutrition_service.log_meal(
                user_id,
                meal_type=args.get("meal_type"),
                description=args.get("description", ""),
                calories=args.get("calories"),
                protein_g=args.get("protein_g"),
                carbs_g=args.get("carbs_g"),
                fat_g=args.get("fat_g"),
                fiber_g=args.get("fiber_g"),
                source=args.get("source", "ai_estimated"),
                vitamin_d_mcg=args.get("vitamin_d_mcg"),
                magnesium_mg=args.get("magnesium_mg"),
                zinc_mg=args.get("zinc_mg"),
                iron_mg=args.get("iron_mg"),
                b12_mcg=args.get("b12_mcg"),
                potassium_mg=args.get("potassium_mg"),
                vitamin_c_mg=args.get("vitamin_c_mg"),
                calcium_mg=args.get("calcium_mg"),
                sodium_mg=args.get("sodium_mg"),
            )
            daily = nutrition_service.get_daily_intake(user_id)
            result = {
                "logged": {
                    "meal_type": meal.get("meal_type"),
                    "description": meal.get("description"),
                    "calories": meal.get("calories"),
                    "protein_g": meal.get("protein_g"),
                    "carbs_g": meal.get("carbs_g"),
                    "fat_g": meal.get("fat_g"),
                    "source": meal.get("source"),
                },
                "daily_total": {
                    "meals": daily["meal_count"],
                    "calories": daily["total_calories"],
                    "protein": daily["total_protein"],
                    "carbs": daily["total_carbs"],
                    "fat": daily["total_fat"],
                },
                "remaining": daily.get("remaining", {}),
                "targets": daily.get("targets", {}),
            }
            # Include micronutrient totals if available
            micros = daily.get("micros", {})
            if any(v > 0 for v in micros.values()):
                result["daily_micros"] = micros
            return result

        elif name == "get_daily_nutrition":
            from bot.services import nutrition_service
            daily = nutrition_service.get_daily_intake(user_id)
            meals = nutrition_service.get_meals_today(user_id)
            result = {
                "date": daily["date"],
                "meal_count": daily["meal_count"],
                "meals": [{"type": m.get("meal_type"), "description": m.get("description"),
                           "calories": m.get("calories"), "protein_g": m.get("protein_g"),
                           "source": m.get("source")}
                          for m in meals],
                "totals": {
                    "calories": daily["total_calories"],
                    "protein": daily["total_protein"],
                    "carbs": daily["total_carbs"],
                    "fat": daily["total_fat"],
                },
                "targets": daily.get("targets", {}),
                "remaining": daily.get("remaining", {}),
            }
            # Include daily micronutrient totals
            micros = daily.get("micros", {})
            if any(v > 0 for v in micros.values()):
                result["daily_micros"] = micros
            # Include 7-day micro trends and flag deficiencies
            try:
                trends = nutrition_service.get_micro_trends(user_id, days=7)
                if trends:
                    result["micro_7d_averages"] = trends
                    # Flag deficiencies (below 50% of RDA)
                    rda = {"vitamin_d_mcg": 15, "magnesium_mg": 400, "zinc_mg": 11,
                           "iron_mg": 8, "b12_mcg": 2.4, "potassium_mg": 2600,
                           "vitamin_c_mg": 90, "calcium_mg": 1000, "sodium_mg": 2300}
                    deficiencies = []
                    for key, target in rda.items():
                        avg = trends.get(key, 0)
                        if avg < target * 0.5:
                            deficiencies.append(f"{key}: avg {avg:.1f} vs RDA {target}")
                    if deficiencies:
                        result["potential_deficiencies"] = deficiencies
            except Exception:
                pass
            return result

        elif name == "report_pain":
            from bot.db.database import get_cursor
            location = args["location"]
            severity = args["severity"]
            pain_type = args.get("pain_type")
            triggers = args.get("triggers")
            description = args.get("description")
            onset = args.get("onset")

            # Upstream/downstream analysis based on joint-by-joint approach
            upstream_map = {
                "knee": {"upstream": "hip", "downstream": "ankle", "likely_cause": "Hip internal rotation deficit or ankle dorsiflexion restriction. Knee compensates with valgus/rotation it was not designed for."},
                "low_back": {"upstream": "thoracic_spine", "downstream": "hip", "likely_cause": "Hip flexion/extension/rotation deficit or thoracic spine stiffness. Lumbar spine forced into excessive motion."},
                "shoulder": {"upstream": "cervical_spine", "downstream": "thoracic_spine", "likely_cause": "Thoracic kyphosis preventing scapular upward rotation, or weak serratus anterior/lower trap causing impingement."},
                "neck": {"upstream": "head_posture", "downstream": "thoracic_spine", "likely_cause": "Thoracic spine stiffness forcing cervical hyperextension, or anterior chain tightness (pec minor, SCM) pulling head forward."},
                "hip": {"upstream": "lumbar_spine", "downstream": "knee", "likely_cause": "Core instability shifting load to hip, or foot/ankle dysfunction altering hip mechanics."},
                "ankle": {"upstream": "knee", "downstream": "foot", "likely_cause": "Calf/soleus tightness or joint capsule restriction limiting dorsiflexion. Check foot arch stability."},
                "elbow": {"upstream": "shoulder", "downstream": "wrist", "likely_cause": "Shoulder external rotation/rotator cuff weakness forcing forearm muscles to compensate. Address shoulder first."},
                "wrist": {"upstream": "elbow", "downstream": "hand", "likely_cause": "Shoulder/elbow mobility deficit increasing wrist load. Also check forearm tissue quality."},
                "thoracic_spine": {"upstream": "cervical_spine", "downstream": "lumbar_spine", "likely_cause": "Prolonged flexion posture (desk work). Adjacent joints compensate — both neck and low back take excessive load."},
                "foot": {"upstream": "ankle", "downstream": "toes", "likely_cause": "Ankle mobility restriction or deep front line dysfunction. Inner arch collapse often a stability failure, not a flexibility issue."},
            }

            analysis = upstream_map.get(location, {"upstream": "unknown", "downstream": "unknown", "likely_cause": "Assess joints above and below the pain site."})

            # Mobility prescriptions by location
            prescriptions = {
                "knee": "1. Banded ankle distraction 2min/side\n2. Wall ankle mobilization 3x30s/side\n3. 90/90 hip stretch 2min/side\n4. Hip CARs 10/side\n5. Single-leg glute bridge 3x12/side\nDo NOT push through knee pain during squats — reduce depth or switch to box squat.",
                "low_back": "1. Couch stretch 3min/side (hip flexor release)\n2. T-spine foam roller extension 2min segment by segment\n3. Open book rotations 10/side with 3s hold\n4. Hip 90/90 transitions 10 total\n5. Dead bug 3x8/side (core activation)\n6. DNS 90/90 breathing 5min daily\nAvoid loaded spinal flexion until pain-free.",
                "shoulder": "1. T-spine foam roller extension 2min\n2. Peanut on T-spine erectors 2min/segment\n3. Doorway pec stretch 30s x 3 positions/side\n4. Serratus wall slides 3x10\n5. Prone Y-T-W raises 3x8 each\n6. Side-lying external rotation 3x12/side\nAvoid overhead pressing until impingement clears.",
                "neck": "1. T-spine foam roller upper segments 2min\n2. Chin tucks 3x15 with 5s hold\n3. Suboccipital release with tennis ball 2min\n4. Pec minor doorway stretch 2min/side\n5. Deep neck flexor activation 3x10 with 10s hold\n6. Thread the needle 10/side\nReduce screen time, check workstation ergonomics.",
                "hip": "1. Couch stretch 3min/side\n2. Banded hip distraction lateral 2min/side\n3. 90/90 hip stretch with rotation 2min/side\n4. Pigeon stretch 2min/side\n5. Lacrosse ball on glute/piriformis 2min/side\n6. Glute bridge marching 3x10/side",
                "ankle": "1. Banded ankle distraction 2min/side\n2. Wall ankle mobilization 2min/side\n3. Lacrosse ball on calf/soleus 2min/side\n4. Lacrosse ball on plantar fascia 2min/side\n5. Calf raises eccentric 3x15 (slow 3s lower)",
                "elbow": "1. Banded shoulder external rotation 3x15/side\n2. Sleeper stretch 2min/side\n3. Lacrosse ball on infraspinatus 2min/side\n4. Eccentric wrist extensions 3x15 (3-5s lower)\n5. Forearm pronation/supination 3x10\nAddress shoulder mobility FIRST — elbow pain is usually downstream.",
                "wrist": "1. Wrist flexor stretch 4x30s\n2. Wrist extensor stretch 4x30s\n3. Prayer/reverse prayer stretch 4x15s\n4. Finger extensions with band 3x15\n5. Wrist circles 10 each direction\nCheck shoulder and elbow mobility upstream.",
                "thoracic_spine": "1. Foam roller T-spine extension 2min segment by segment\n2. Peanut on erectors 2min/segment\n3. Cat-cow isolating thoracic 15 reps\n4. Thread the needle 10/side\n5. Open book rotations 10/side\n6. Quadruped T-spine rotation 10/side\nAddress hourly if desk worker — movement snacks every 45min.",
                "foot": "1. Lacrosse ball plantar fascia 2min/side\n2. Toe yoga (lift big toe, press others down, then reverse) 3x10\n3. Short foot exercise 3x10 (arch lift without curling toes)\n4. Calf raises 3x15 (barefoot, full ROM)\n5. Banded ankle distraction 2min/side",
            }

            rx = prescriptions.get(location, "Assess the area. Foam roll 2min, stretch 2min, activate 2x12. If pain persists 2+ weeks, see a physio.")

            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO pain_reports (user_id, location, severity, pain_type, triggers, description, onset, upstream_cause, prescription)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (user_id, location, severity, pain_type, triggers, description, onset, analysis["likely_cause"], rx)
                )
                pain_id = cur.fetchone()[0]

            return {
                "pain_id": pain_id,
                "location": location,
                "severity": severity,
                "analysis": {
                    "likely_upstream_cause": analysis["likely_cause"],
                    "check_upstream": analysis["upstream"],
                    "check_downstream": analysis["downstream"],
                },
                "mobility_prescription": rx,
                "training_modification": f"Avoid loading {location} at high intensity until severity drops below 3/10. Modify exercises to pain-free alternatives.",
                "follow_up": "Reassess in 3-5 days. If no improvement or worsening, recommend seeing a physiotherapist.",
            }

        elif name == "get_pain_history":
            from bot.db.database import get_cursor
            status = args.get("status", "active")
            with get_cursor() as cur:
                if status == "all":
                    cur.execute(
                        """SELECT id, location, severity, pain_type, triggers, description, onset,
                                  upstream_cause, prescription, status, created_at, resolved_at
                           FROM pain_reports WHERE user_id = %s ORDER BY created_at DESC LIMIT 20""",
                        (user_id,)
                    )
                else:
                    cur.execute(
                        """SELECT id, location, severity, pain_type, triggers, description, onset,
                                  upstream_cause, prescription, status, created_at, resolved_at
                           FROM pain_reports WHERE user_id = %s AND status = %s ORDER BY created_at DESC LIMIT 20""",
                        (user_id, status)
                    )
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description]
                reports = [dict(zip(cols, r)) for r in rows]
                for r in reports:
                    for k in ("created_at", "resolved_at"):
                        if r.get(k) and hasattr(r[k], "isoformat"):
                            r[k] = r[k].isoformat()
            return {"pain_reports": reports, "count": len(reports)}

        elif name == "resolve_pain":
            from bot.db.database import get_cursor
            pain_id = args["pain_id"]
            notes = args.get("notes", "")
            with get_cursor() as cur:
                cur.execute(
                    """UPDATE pain_reports SET status = 'resolved', resolved_at = NOW(),
                              description = COALESCE(description, '') || %s
                       WHERE id = %s AND user_id = %s RETURNING location, severity""",
                    (f" | Resolution: {notes}" if notes else "", pain_id, user_id)
                )
                row = cur.fetchone()
                if not row:
                    return {"error": "Pain report not found"}
            return {"success": True, "pain_id": pain_id, "location": row[0], "resolved": True}

        else:
            return {"error": f"Unknown tool: {name}"}

    except Exception as e:
        logger.error(f"Tool {name} failed: {type(e).__name__}: {e}")
        return {"error": f"Tool temporarily unavailable. Please try again."}
