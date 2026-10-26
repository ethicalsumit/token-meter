# token-meter

A Claude plugin that shows how many tokens the current chat has used, with `/tokens`.

It reads the usage the API reported for every model call in the chat (main thread and subagents) from the chat's own session transcript, and weights it into one "effective" number that follows cost: cache reads at ×0.1, cache writes at ×2, output at ×5 and plain input at ×1.

## Commands

| Command | What it shows | Cost to run |
| :--- | :--- | :--- |
| `/tokens` | Detailed card: effective total, estimated API cost, share bar, one row per usage type, context of the last call, model calls, cache hit rate, thinking tokens, chat duration, tool calls and the most used tools | Highest, it draws a card |
| `/tokens summary` | Summary card: effective total, estimated API cost, share bar, last message and context of the last call | High, it draws a card |
| `/tokens lite` | The summary as text: a table sorted by share with a bar of colored squares per row, then last message and context | Lowest, plain text |
| `/tokens help` | Short help | Lowest, plain text |

**Last message** is the effective tokens of the message before the current one, so `/tokens` never counts itself. It appears once the chat has an earlier message in its session.

**API cost** prices every model call in the chat at Anthropic's published API list prices for its model (input, cache writes, cache reads and output each at their own rate). It is not the bill on a Claude plan. A model missing from the price table is priced at the session's own cost rate when the session has written one.

**Cards** render where the chat can draw inline visuals. Elsewhere `/tokens` and `/tokens summary` fall back to the lite view. Drawing a card adds noticeably to the chat's own usage, so use `/tokens lite` to check often.

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
