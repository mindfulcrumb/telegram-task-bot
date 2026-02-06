# Anthropic API Troubleshooting Guide

## Lessons Learned (Feb 2026)

### Issue: `/analyze` command failing with various API errors

**Time wasted**: Several hours debugging encoding issues when the real problems were:
1. Invalid/revoked API key
2. Account lacking access to the requested model

---

## Key Debugging Steps (Do These FIRST)

### 1. Test API Key Locally Before Anything Else

```bash
ANTHROPIC_API_KEY="your-key-here" python3 -c "
import anthropic
client = anthropic.Anthropic()
msg = client.messages.create(
    model='claude-3-haiku-20240307',
    max_tokens=100,
    messages=[{'role': 'user', 'content': 'Say hi'}]
)
print(msg.content[0].text)
"
```

If this fails locally, the problem is the key or model - NOT your deployment environment.

### 2. Check Model Access

**Not all Anthropic accounts have access to all models.**

| Model | Access Level |
|-------|--------------|
| `claude-3-haiku-20240307` | Most accounts |
| `claude-3-5-sonnet-20241022` | May require higher tier |
| `claude-3-opus-*` | Usually requires paid tier |

**Error when model not available:**
```
not_found_error: model: claude-3-5-sonnet-20241022
```

**Fix:** Use a model your account has access to (haiku is safest default).

---

## Common Errors and Actual Causes

### `invalid x-api-key`
- **NOT** an encoding issue
- **NOT** Railway corrupting the key
- **ACTUAL CAUSE**: Key is revoked, expired, or incorrectly copied

**Fix:** Generate a fresh key at https://console.anthropic.com/settings/keys

### `not_found_error: model`
- **ACTUAL CAUSE**: Account doesn't have access to that model tier

**Fix:** Use `claude-3-haiku-20240307` instead of sonnet/opus

### `UnicodeEncodeError` in Railway
- Can be a real encoding issue in minimal Docker environments
- BUT: Often masks the real error (like invalid key)
- Always test locally first to rule out deployment-specific issues

---

## Railway-Specific Issues

### Environment Variable Quoting
Railway's UI sometimes auto-adds quotes around values. The `config.py` has `clean_env_value()` to handle this:

```python
def clean_env_value(value):
    """Strip whitespace AND quotes from env vars."""
    if not value:
        return ""
    value = value.strip()
    if len(value) >= 2:
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
    return value.strip()
```

### Multiple Bot Instances
Telegram only allows one polling connection per bot. If you see:
```
Conflict: terminated by other getUpdates request
```

**Fix:** Wait for old deployments to stop, or restart the service in Railway dashboard.

---

## Current Working Configuration

```python
# bot/ai/brain.py
model="claude-3-haiku-20240307"  # Works with most accounts
```

```python
# config.py
ANTHROPIC_API_KEY = clean_env_value(os.getenv("ANTHROPIC_API_KEY"))
```

---

## Debugging Checklist

Before spending hours on encoding/deployment issues:

- [ ] Test API key locally with simple script
- [ ] Verify key is not revoked in Anthropic console
- [ ] Confirm account has access to the model being used
- [ ] Try `claude-3-haiku-20240307` as baseline
- [ ] Check Railway logs for actual error messages
- [ ] Ensure only one bot instance is running

---

## The Hard Lesson

> "We could have saved a lot of time if we understood that the API didn't use that specific model"

**Always verify model access first.** A 404 on the model endpoint means your account tier doesn't include that model - not that your code is broken.
