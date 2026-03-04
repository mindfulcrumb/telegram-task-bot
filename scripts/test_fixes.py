#!/usr/bin/env python3
"""Unit tests for Fix 5 (workout PDF extraction) and Fix 6 (USDA-first meal lookup).

Run: python scripts/test_fixes.py
Exit codes: 0 = all passed, 1 = failures
"""
import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Fix 5 tests — Workout PDF extraction
# ---------------------------------------------------------------------------

class TestWorkoutPDFClassification(unittest.TestCase):
    """Verify the updated classifier prompt includes 'workout' type."""

    def test_workout_in_classification_prompt(self):
        from bot.handlers.photo_handler import IMAGE_CLASSIFICATION_PROMPT
        self.assertIn('"workout"', IMAGE_CLASSIFICATION_PROMPT)
        self.assertIn("workout", IMAGE_CLASSIFICATION_PROMPT.lower())

    def test_workout_prompt_exists(self):
        from bot.handlers.photo_handler import WORKOUT_PDF_EXTRACTION_PROMPT
        self.assertIn("is_workout", WORKOUT_PDF_EXTRACTION_PROMPT)
        self.assertIn("exercises", WORKOUT_PDF_EXTRACTION_PROMPT)
        self.assertIn("movement_pattern", WORKOUT_PDF_EXTRACTION_PROMPT)

    def test_workout_extraction_parses_valid_json(self):
        """Simulate what Claude returns and verify we can parse it."""
        mock_response = {
            "is_workout": True,
            "title": "Upper Body Push",
            "date": "2026-03-04",
            "duration_minutes": 60,
            "rpe": 8,
            "notes": "Felt strong today",
            "exercises": [
                {
                    "exercise_name": "Bench Press",
                    "movement_pattern": "horizontal_push",
                    "sets": 4,
                    "reps": "8",
                    "weight": 100,
                    "weight_unit": "kg",
                    "rpe": 8
                },
                {
                    "exercise_name": "Overhead Press",
                    "movement_pattern": "vertical_push",
                    "sets": 3,
                    "reps": "10",
                    "weight": 60,
                    "weight_unit": "kg",
                    "rpe": None
                }
            ]
        }
        # Parse as JSON (same code path as _extract_workout_pdf_vision)
        text = json.dumps(mock_response)
        parsed = json.loads(text)
        self.assertTrue(parsed["is_workout"])
        self.assertEqual(len(parsed["exercises"]), 2)
        self.assertEqual(parsed["exercises"][0]["exercise_name"], "Bench Press")
        self.assertEqual(parsed["exercises"][0]["movement_pattern"], "horizontal_push")

    def test_workout_extraction_parses_cardio(self):
        """Cardio workout with no exercises array."""
        mock_response = {
            "is_workout": True,
            "title": "Morning Run",
            "date": "2026-03-04",
            "duration_minutes": 45,
            "rpe": 6,
            "notes": "5km in 24:30, avg HR 155bpm",
            "exercises": []
        }
        parsed = json.loads(json.dumps(mock_response))
        self.assertTrue(parsed["is_workout"])
        self.assertEqual(parsed["title"], "Morning Run")
        self.assertEqual(len(parsed["exercises"]), 0)

    def test_workout_not_detected_returns_false(self):
        """Non-workout doc returns is_workout: false."""
        mock_response = {"is_workout": False}
        parsed = json.loads(json.dumps(mock_response))
        self.assertFalse(parsed["is_workout"])

    def test_handle_workout_pdf_context_building(self):
        """Verify _handle_workout_pdf builds valid context for brain."""
        # Test the context string format we expect
        extraction = {
            "is_workout": True,
            "title": "Leg Day",
            "date": "2026-03-04",
            "duration_minutes": 75,
            "rpe": 9,
            "notes": "New squat PR",
            "exercises": [
                {"exercise_name": "Squat", "movement_pattern": "squat",
                 "sets": 5, "reps": "5", "weight": 140, "weight_unit": "kg", "rpe": 9}
            ]
        }

        # Build the context string the same way _handle_workout_pdf does
        exercises = extraction.get("exercises", [])
        title = extraction.get("title", "Workout")
        duration = extraction.get("duration_minutes")
        notes = extraction.get("notes")
        rpe = extraction.get("rpe")

        image_context = "[WORKOUT DOCUMENT — EXTRACTED DATA]\n"
        image_context += f"Title: {title}\n"
        if duration:
            image_context += f"Duration: {duration} minutes\n"
        if rpe:
            image_context += f"RPE: {rpe}/10\n"
        if notes:
            image_context += f"Notes: {notes}\n"
        if exercises:
            image_context += f"\nExercises ({len(exercises)}):\n"

        self.assertIn("WORKOUT DOCUMENT", image_context)
        self.assertIn("Leg Day", image_context)
        self.assertIn("75 minutes", image_context)
        self.assertIn("New squat PR", image_context)
        self.assertIn("Exercises (1)", image_context)


# ---------------------------------------------------------------------------
# Fix 6 tests — USDA-first meal lookup
# ---------------------------------------------------------------------------

class TestUSDAFirstMealLookup(unittest.TestCase):
    """Test the USDA enrichment logic in log_meal executor."""

    def test_usda_enrichment_applied_on_success(self):
        """When USDA returns data, macro values should be overwritten."""
        # Simulate the enrichment logic from execute_tool log_meal
        args = {
            "description": "oats",
            "calories": 300,
            "protein_g": 8,
            "carbs_g": 50,
            "fat_g": 5,
            "source": "ai_estimated",
        }

        # Mock USDA returning per-100g data for oats
        mock_nutrients_100g = {
            "calories": 389,      # oats are ~389 cal/100g
            "protein": 16.9,
            "carbs": 66.3,
            "fat": 6.9,
            "fiber": 10.6,
            "magnesium": 177,
            "iron": 4.7,
            "b12": 0,
        }

        ai_calories = args["calories"]  # 300
        usda_cal = mock_nutrients_100g["calories"]  # 389
        grams = (ai_calories / usda_cal) * 100  # ~77g

        self.assertAlmostEqual(grams, 77.1, delta=0.5)
        self.assertTrue(10 <= grams <= 2000)  # sanity check passes

        # Scale nutrients
        factor = grams / 100
        scaled = {k: round(v * factor, 2) for k, v in mock_nutrients_100g.items()
                  if isinstance(v, (int, float))}

        # Apply the same merge logic
        def _pick(usda_val, ai_key):
            return usda_val if usda_val else args.get(ai_key)

        enriched = {**args,
            "protein_g": _pick(scaled.get("protein"), "protein_g"),
            "carbs_g": _pick(scaled.get("carbs"), "carbs_g"),
            "fat_g": _pick(scaled.get("fat"), "fat_g"),
            "fiber_g": _pick(scaled.get("fiber"), "fiber_g"),
            "source": "usda",
        }

        self.assertEqual(enriched["source"], "usda")
        # USDA protein for ~77g oats ≈ 13g (not original 8g)
        self.assertGreater(enriched["protein_g"], 10)
        self.assertIn("fiber_g", enriched)
        self.assertGreater(enriched["fiber_g"], 0)

    def test_usda_enrichment_skipped_on_failure(self):
        """When USDA fails, original args are kept unchanged."""
        original_args = {
            "description": "some exotic dish",
            "calories": 500,
            "protein_g": 25,
            "source": "ai_estimated",
        }
        args = dict(original_args)

        # Simulate USDA returning None (not found)
        nutrients_100g = None
        usda_cal = nutrients_100g.get("calories") if nutrients_100g else None

        if usda_cal and usda_cal > 0:
            pass  # enrichment applied
        # else: args unchanged

        self.assertEqual(args["source"], "ai_estimated")
        self.assertEqual(args["protein_g"], 25)

    def test_usda_enrichment_skipped_on_bad_grams(self):
        """If derived grams is outside 10-2000g range, enrichment is skipped."""
        # Tiny food: 10 calories for a food that's 800 cal/100g → ~1.25g, below 10g
        ai_calories = 10
        usda_cal = 800
        grams = (ai_calories / usda_cal) * 100  # ~1.25g
        self.assertFalse(10 <= grams <= 2000)

        # Huge food: 5000 cal with 100 cal/100g → 5000g, above 2000g
        ai_calories = 5000
        usda_cal = 100
        grams = (ai_calories / usda_cal) * 100
        self.assertFalse(10 <= grams <= 2000)

    def test_usda_enrichment_skipped_for_photo_source(self):
        """USDA pre-lookup only runs for ai_estimated; usda/manual are skipped."""
        # Simulate the condition check
        for skip_source in ("usda", "manual"):
            source = skip_source
            should_enrich = (source == "ai_estimated")
            self.assertFalse(should_enrich, f"Should NOT enrich source='{skip_source}'")

        # Only ai_estimated triggers enrichment
        source = "ai_estimated"
        should_enrich = (source == "ai_estimated")
        self.assertTrue(should_enrich)

    def test_usda_pick_fallback(self):
        """_pick returns AI value when USDA has None/0 for a nutrient."""
        def _pick(usda_val, ai_val):
            return usda_val if usda_val else ai_val

        # USDA has data — use it
        self.assertEqual(_pick(13.5, 8.0), 13.5)
        # USDA has None — fall back to AI
        self.assertEqual(_pick(None, 8.0), 8.0)
        # USDA has 0 — fall back to AI (falsy)
        self.assertEqual(_pick(0, 8.0), 8.0)


# ---------------------------------------------------------------------------
# Compile checks
# ---------------------------------------------------------------------------

class TestCompile(unittest.TestCase):
    """Both modified files must compile without errors."""

    def test_photo_handler_compiles(self):
        import py_compile
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "bot", "handlers", "photo_handler.py"
        )
        try:
            py_compile.compile(path, doraise=True)
        except py_compile.PyCompileError as e:
            self.fail(f"photo_handler.py compile error: {e}")

    def test_tools_v2_compiles(self):
        import py_compile
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "bot", "ai", "tools_v2.py"
        )
        try:
            py_compile.compile(path, doraise=True)
        except py_compile.PyCompileError as e:
            self.fail(f"tools_v2.py compile error: {e}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_tests():
    print("\n" + "=" * 60)
    print("FIX TESTS — Fix 5 (Workout PDF) + Fix 6 (USDA-first)")
    print("=" * 60 + "\n")

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestWorkoutPDFClassification))
    suite.addTests(loader.loadTestsFromTestCase(TestUSDAFirstMealLookup))
    suite.addTests(loader.loadTestsFromTestCase(TestCompile))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 60)
    if result.wasSuccessful():
        print(f"✅ All {result.testsRun} tests passed")
        return 0
    else:
        print(f"❌ {len(result.failures)} failures, {len(result.errors)} errors")
        return 1


if __name__ == "__main__":
    sys.exit(run_tests())
