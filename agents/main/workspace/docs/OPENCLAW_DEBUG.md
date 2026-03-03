---
name: openclaw-debug
description: Lessons learned from 2026-03-03 setup session — diagnose and fix common OpenClaw issues
---

# OpenClaw Debug & Lessons Learned

## Model Tier Rules (CRITICAL)
- tier1 (claude-sonnet-4-6): ALL Telegram messages, slash commands, trading decisions — only model that reliably follows 31K system prompt
- tier2 (claude-haiku-4-5): IDLE for now — too weak for large system prompts, ignores workspace files, responds as generic chatbot
- tier3 (gpt-5-mini): heartbeat ONLY — no workspace access needed, too weak for any instruction-following
- Golden rule: never assign Telegram slash commands to tier2 or tier3

## Slash Command Architecture (What We Learned)
- disable-model-invocation: true does NOT create direct execution — message still goes to LLM
- command-dispatch: tool only works if there is text AFTER the slash command — /report has nothing after it, so exec gets empty string
- Correct approach: LLM-invokable skill with explicit bash command in SKILL.md body
- SKILL.md must have non-empty description or it will NOT load
- All skills must show ✓ ready in: openclaw skills list

## Session Management
- Orphaned session references cause silent routing failures — check sessions.json for dead .jsonl references
- Clean stale sessions before debugging any routing issue
- After session cleanup: restart gateway

## Telegram Delivery Rules
- chatId must be mapped in active session
- slash commands only work through native Telegram channel, NOT via CLI --deliver flag
- Test delivery directly: python3 /Users/wai/.openclaw/workspace/tools/slash_cmd.py report --send

## Skill Loading Rules
- Empty description in SKILL.md frontmatter = skill not loaded (silent failure)
- workspace skills take priority over bundled skills
- Always restart gateway after adding/changing skills
- Verify: openclaw skills list --verbose

## Debugging Checklist (run in this order)
1. openclaw gateway health
2. openclaw skills list
3. openclaw channels status --probe
4. openclaw models status
5. tail -50 /tmp/openclaw/openclaw-$(date +%Y-%m-%d).log
6. openclaw doctor

## Golden Rules
- Always backup openclaw.json before ANY major config change
- dry-run before live trading after ANY code change
- One fix at a time — diagnose first, fix second
- slash_cmd.py is source of truth for all report formatting
- If something is broken: check logs first, ask agent to diagnose before fixing
