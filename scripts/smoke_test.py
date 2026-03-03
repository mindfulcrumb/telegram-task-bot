#!/usr/bin/env python3
"""Smoke test — verify all critical bot systems before deploy.

Run before every push: python scripts/smoke_test.py && git push

Exit codes:
  0 = all checks passed ✓
  1 = one or more checks failed ✗
"""
import os
import sys
import logging

# Set up minimal logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Add repo to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_environment_variables():
    """Check that critical env vars are set."""
    required = [
        "TELEGRAM_BOT_TOKEN",
        "DATABASE_URL",
        "ANTHROPIC_API_KEY",
    ]
    results = []
    for var in required:
        is_set = bool(os.getenv(var))
        results.append((f"Env: {var}", is_set))
    return results


def test_imports():
    """Check that all critical modules import without error."""
    results = []
    imports_to_test = [
        ("bot.main_v2", None),
        ("bot.ai.brain_v2", "AIBrain"),
        ("bot.ai.tools_v2", "get_tools"),
        ("bot.db.database", "get_pool"),
        ("bot.services.fitness_service", "FitnessService"),
        ("bot.services.whoop_service", "WhoopService"),
        ("bot.services.user_service", "UserService"),
    ]

    for module_name, class_name in imports_to_test:
        try:
            module = __import__(module_name, fromlist=[class_name] if class_name else [])
            if class_name:
                getattr(module, class_name)
            results.append((f"Import: {module_name}", True))
        except Exception as e:
            results.append((f"Import: {module_name}", False, str(e)))

    return results


def test_anthropic_api():
    """Test that Anthropic API is callable."""
    results = []
    try:
        from anthropic import Anthropic

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return [("Anthropic API", False, "ANTHROPIC_API_KEY not set")]

        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": "ping"}],
        )
        results.append(("Anthropic API", True))
    except Exception as e:
        results.append(("Anthropic API", False, str(e)))

    return results


def test_database_connection():
    """Test that PostgreSQL connection pool can be created."""
    results = []
    try:
        from bot.db.database import get_pool

        pool = get_pool()
        conn = pool.getconn()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        pool.putconn(conn)
        results.append(("Database connection", True))
    except Exception as e:
        results.append(("Database connection", False, str(e)))

    return results


def main():
    """Run all smoke tests."""
    print("\n" + "=" * 60)
    print("SMOKE TEST — Pre-Deploy Verification")
    print("=" * 60 + "\n")

    all_results = []

    print("Checking environment variables...")
    all_results.extend(test_environment_variables())

    print("Checking imports...")
    all_results.extend(test_imports())

    print("Checking Anthropic API...")
    all_results.extend(test_anthropic_api())

    print("Checking database connection...")
    all_results.extend(test_database_connection())

    # Print results
    print("\n" + "=" * 60)
    print("RESULTS:")
    print("=" * 60)

    passed = 0
    failed = 0

    for check_name, status, *error in all_results:
        if status:
            print(f"  ✓ {check_name}")
            passed += 1
        else:
            print(f"  ✗ {check_name}")
            if error:
                print(f"    Error: {error[0][:100]}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"SUMMARY: {passed} passed, {failed} failed")
    print("=" * 60 + "\n")

    if failed > 0:
        print("❌ SMOKE TEST FAILED — do not deploy")
        return 1
    else:
        print("✅ All checks passed — safe to deploy")
        return 0


if __name__ == "__main__":
    sys.exit(main())
