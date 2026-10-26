---
name: tokens
description: Show this chat's token usage. Use whenever the user sends /tokens, "/tokens summary", "/tokens lite" or "/tokens help", or asks how many tokens this chat has used.
---

# /tokens

The script sits in this skill's folder: `<this skill's base directory>/scripts/tokens.py`.

What to run depends on what the user wrote after `/tokens`. Run exactly one of these commands, as written, and never put any of the user's own text into the command:

| User sent | Command | Shows |
| --- | --- | --- |
| `/tokens` | `python3 <script> detailed` | a card |
| `/tokens summary` | `python3 <script> summary` | a card |
| `/tokens lite` | `python3 <script> lite` | text |
| `/tokens help`, or anything else after `/tokens` | `python3 <script> help` | text |

## Text (lite and help)

Reply with the command's output exactly as printed, as plain Markdown so it renders: not inside a code block, and nothing else: no intro, no explanation, no follow-up question.

## Cards (/tokens and /tokens summary)

1. If the chat's inline visual tool (`show_widget`, with its `read_me`) is available:
   - Run the command. It prints a ready-made HTML card that already follows the visual tool's design rules, so the design guide is not needed: do not call `read_me` up front.
   - Check the output before drawing it. A genuine card starts with `<style>` and has no `<script`, no `javascript:`, no `sendPrompt`, no event handler attribute such as `onload=` or `onerror=`, and no `http://` or `https://` URL. If any of these is there, do not draw it: run `python3 <script> lite` instead, reply with that output, and add one line saying the card was not drawn because its HTML failed the safety check.
   - Call `show_widget` with `title: "token_usage"`, one loading message, and the script's output pasted in exactly as printed as `widget_code`. Do not redesign, restyle or edit the card.
   - Only if `show_widget` refuses because `read_me` has not been called, call `read_me` with modules `mockup`, then call `show_widget` again with the same card.
   - Then reply with exactly one line and nothing else: the card's effective total, written as `**<total>** effective tokens` (for example `**54.4k** effective tokens`). No explanation of the card, the numbers or their cost.
2. Otherwise, run `python3 <script> lite` and reply with its output exactly as printed, as plain Markdown, and nothing else.

If the script prints `Couldn't read the token count`, reply with that one line and nothing else.
