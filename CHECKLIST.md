# Pre-Push Checklist

**NEVER push to main without completing this checklist.**

## Before Every Push

- [ ] Run `python scripts/smoke_test.py` — all checks pass (green ✓)
- [ ] Check Railway health: bot health endpoint is green (no errors in last 5 min)
- [ ] Update CLAUDE.md with:
  - [ ] What changed in this session
  - [ ] What's broken or pending
  - [ ] Next priorities
- [ ] No untracked scripts left in `scripts/` directory that aren't committed
- [ ] No `.env` files or secrets committed (check `.gitignore`)

## Last Verified Working

| Field | Value |
|-------|-------|
| **Date** | _MM-DD-YYYY_ |
| **Commit** | _abc1234_ |
| **Verified by** | smoke_test.py |
| **Railway status** | green / red |
| **Notes** | _any known issues?_ |

---

## Session Template (copy to CLAUDE.md after each session)

```markdown
### Session N — Mar DD, 2026

**What was done:**
1. Fixed X in file Y
2. Added Z feature
3. Deployed to Railway

**Status:**
- ✓ All imports load
- ✓ DB connection works
- ✓ Anthropic API working
- ✓ Railway deployed

**What's next:**
1. Test OAuth flow
2. Verify Strava sync
3. Consider Oura Ring integration

**Known issues:**
- Timezone mismatch in streaks (lower priority)
```

---

## Deployment Checklist (after git push)

- [ ] GitHub Action CI passes (if configured)
- [ ] Railway auto-deploy triggered (check Railway dashboard)
- [ ] Railway health check green (wait 2 min)
- [ ] Test bot with `/start` command — responds
- [ ] No error messages in Railway logs (check `railway logs`)

### Voice Handler Smoke Test (required if any voice_v2.py changes)

- [ ] Send a voice note to @Zoe
- [ ] Check Railway logs for "Whisper detected language" (confirms Groq transcription worked)
- [ ] Verify Zoe responds (not "Didn't catch that")
- [ ] Check for "messages.*: Input should be a valid" errors in logs (API format error)
- [ ] Send 2-3 more voice notes in quick succession (test no race conditions)
- [ ] Check response time is <10s (target from PERF fixes)

---

## Emergency Rollback

If Railway bot is broken after deploy:

```bash
# View last 5 commits
git log --oneline -5

# Revert to previous commit
git revert HEAD
git push

# Check Railway status
railway logs
```

---

## Red Flags — Don't Push

- ❌ smoke_test.py shows ✗ (any failed check)
- ❌ Untracked Python files in repo root or `bot/`
- ❌ CLAUDE.md not updated with session summary
- ❌ `bot.error.log` > 1MB (indicates crash loop)
- ❌ Any `import psycopg2` inside async functions (blocks event loop)
- ❌ Any sync httpx calls in handlers (should use `asyncio.to_thread()`)
- ❌ `asyncio.to_thread()` wrapping data fetches passed to Claude API — can break message serialization
