# token-meter

A Claude plugin that shows how many tokens the current chat has used, with `/tokens`.

It reads the usage the API reported for every model call in the chat (main thread and subagents) from the chat's own session transcript, and weights it into one "effective" number that follows cost: cache reads at ×0.1, cache writes at ×2, output at ×5 and plain input at ×1.

## Commands

| Command | What it shows | Cost to run |
| :--- | :--- | :--- |
| `/tokens` | Detailed card: effective total, estimated API cost, share bar, one row per usage type, context of the last call, model calls, cache hit rate, thinking tokens, chat duration, tool calls and the most used tools | Highest, it draws a card |
| `/tokens summary` | Summary card: effective total, estimated API cost, share bar, last message and context of the last call | High, it draws a card |
| `/tokens lite` | The summary as text: a table sorted by share with a bar of colored squares per row, last message, context and a health light | Lowest, plain text |
| `/tokens history` | Your last 10 messages: model calls, effective tokens and cost of each, with the most expensive marked | Lowest, plain text |
| `/tokens where` | What fills the context that every call re-reads: chat setup, your messages, Claude's replies, screenshots, each tool's output, and the biggest single items | Lowest, plain text |
| `/tokens health` | How heavy the chat has become, what each call now costs, and the cache timer: what the next message costs after a long pause | Lowest, plain text |
| `/tokens tips` | Up to five ways to spend less, each backed by this chat's own numbers | Lowest, plain text |
| `/tokens compare` | This chat's tokens priced on other Claude models | Lowest, plain text |
| `/tokens handoff` | A short note to paste into a new chat, so the work continues without this chat's long context | Low, Claude writes a short note |
| `/tokens help` | Short help | Lowest, plain text |

**Last message** is the effective tokens of the message before the current one, so `/tokens` never counts itself. It appears once the chat has an earlier message in its session.

**API cost** prices every model call in the chat at Anthropic's published API list prices for its model (input, cache writes, cache reads and output each at their own rate). It is not the bill on a Claude plan. A model missing from the price table is priced at the session's own cost rate when the session has written one.

**Where** and **tips** estimate each part of the context from the size of its text and images (about 3.6 characters per token; images by pixel size), then scale the parts to the real context size the API reported. Treat them as close estimates, not exact counts.

**Cache timer:** Claude keeps the chat's context in a prompt cache for a while after each reply (here usually an hour). After a longer pause the next call writes the whole context again at the cache-write price, which `/tokens health` shows next to the usual cache-read price.

**Cards** render where the chat can draw inline visuals. Elsewhere `/tokens` and `/tokens summary` fall back to the lite view. Drawing a card adds noticeably to the chat's own usage, so use `/tokens lite` to check often.

## Requirements

- Claude only: claude.ai (web, desktop and mobile) or Claude Code. It reads Claude's own session transcript, so it does not work with other assistants or with apps built directly on the API.
- The chat must let Claude run code, since the command runs a small Python script.
- Not counted: background calls the app makes outside the chat transcript.

## Install

**claude.ai (web, desktop and mobile):** open Settings, then Plugins, then Add, then Add marketplace, and enter `ethicalsumit/token-meter`. Then add the Token meter plugin.

**Claude Code (terminal):**

```
/plugin install token-meter --marketplace ethicalsumit/token-meter
```

Answer `y` to add the marketplace, then pick a scope.

## What runs on your account

- **The skill** (`skills/tokens`): its instructions reach Claude when you type `/tokens`, and Claude runs `tokens.py`. The script only reads the current chat's own transcript file, makes no network calls and writes nothing.
- **The hooks module** (`hooks/register.tsx`): it runs in every session where the plugin is on, in claude.ai chats as well as the terminal. It only adds up token usage for a short status line, where the app draws one. It registers no command and does not touch prompts.

A plugin's code and instructions run with the same access Claude has in your chats. With automatic sync on, every new commit here reaches your account on its own. If you install it, consider turning sync off and using Check for updates (under Manage marketplaces) after reading the changes.

## License

MIT
