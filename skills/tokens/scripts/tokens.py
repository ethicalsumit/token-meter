#!/usr/bin/env python3
"""Print this chat's token usage, read from the session transcript.

Every model call in a Claude session is written to a transcript under
~/.claude/projects/<folder>/<session-id>.jsonl, with the usage the API
reported for it. This script picks the current session's transcript (by
the session id the environment gives, else the newest one), adds subagent
transcripts kept beside it, and sums the usage once per model call.

Usage:
  tokens.py detailed   the detailed card (HTML), for /tokens
  tokens.py summary    the summary card (HTML), for /tokens summary
  tokens.py lite       the summary as Markdown text, for /tokens lite (also the default)
  tokens.py history    what each of the last 10 messages cost
  tokens.py where      what fills the context every call re-reads
  tokens.py health     how heavy the chat is, and the cache timer
  tokens.py tips       ways to spend less, from this chat's numbers
  tokens.py compare    this chat priced on other models
  tokens.py help       the help text
"""
import base64
import glob
import html
import json
import math
import os
import re
import sys
from datetime import datetime, timezone

HELP = "\n".join([
    "### 🔢 /tokens help",
    "",
    "Counts the tokens this chat has used, from the chat's own session transcript.",
    "",
    "| Command | What it shows |",
    "| :--- | :--- |",
    "| `/tokens` | the detailed view |",
    "| `/tokens summary` | a short summary |",
    "| `/tokens lite` | a text-only view, the cheapest to run |",
    "| `/tokens history` | what each of your last 10 messages cost |",
    "| `/tokens where` | what fills the context that every call re-reads |",
    "| `/tokens health` | how heavy the chat has become, and the cache timer |",
    "| `/tokens tips` | ways to spend less, from this chat's own numbers |",
    "| `/tokens compare` | what this chat would cost on other models |",
    "| `/tokens handoff` | a short note to carry this work into a new chat |",
    "| `/tokens help` | this help |",
    "",
    "Effective tokens weight cache reads at ×0.1, cache writes at ×2 and output at ×5.",
])

KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def fmt(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(round(n))


def fmt_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m"
    return f"{seconds}s"


def tool_label(name):
    """mcp__claude-in-chrome__browser_batch -> Chrome: browser batch."""
    if not name.startswith("mcp__"):
        return name
    parts = name.split("__")
    server = parts[1] if len(parts) > 1 else ""
    tool = " ".join(parts[2:]) if len(parts) > 2 else ""
    if server == "remote-devices" and len(parts) > 3:
        server, tool = parts[2], " ".join(parts[3:])
    server = {"claude-in-chrome": "Chrome", "Claude_Browser": "Browser",
              "claude_ai": "Claude", "memory": "Memory"}.get(server, server.replace("_", " "))
    tool = tool.replace("_", " ").strip()
    return f"{server}: {tool}" if tool else server


def current_transcript():
    """The transcript of this session: by its id when the environment gives one, else the newest
    transcript of the folder this runs in. Never another folder's, so it cannot show another chat."""
    root = os.path.expanduser("~/.claude/projects")
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if re.fullmatch(r"[A-Za-z0-9-]{8,64}", sid):
        named = glob.glob(os.path.join(root, "*", sid + ".jsonl"))
        if named:
            return max(named, key=os.path.getmtime)
    here = os.path.join(root, re.sub(r"[^A-Za-z0-9]", "-", os.getcwd()))
    files = glob.glob(os.path.join(glob.escape(here), "*.jsonl"))
    return max(files, key=os.path.getmtime) if files else None


def num(v):
    """A token count as a non-negative int; anything else (text, NaN, infinity, negative) is 0."""
    if isinstance(v, bool):
        return 0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(f) or f < 0:
        return 0
    return int(min(f, 1e13))


def clean_usage(u):
    """Keep only the usage fields this script reads, each as a safe number."""
    split = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else None
    details = u.get("output_tokens_details") if isinstance(u.get("output_tokens_details"), dict) else {}
    out = {k: num(u.get(k)) for k in KEYS}
    out["thinking_tokens"] = num(details.get("thinking_tokens"))
    out["cache_1h"] = num(split.get("ephemeral_1h_input_tokens")) if split is not None else None
    return out


NOTE_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


def est_chars(n):
    """Rough token count for n characters of text (about 3.6 characters per token)."""
    return int(n / 3.6) if n > 0 else 0


def est_text(text):
    return est_chars(len(text)) if isinstance(text, str) else 0


def image_tokens(source):
    """Tokens an image costs, from its pixel size (width x height / 750, at most about 1,600)."""
    cap = 1600
    if not isinstance(source, dict) or not isinstance(source.get("data"), str):
        return cap
    try:
        raw = base64.b64decode(source["data"][:400_000], validate=False)
    except (ValueError, TypeError):
        return cap
    w = h = 0
    if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) >= 24:
        w, h = int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
    elif raw[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(raw):
            if raw[i] != 0xFF:
                i += 1
                continue
            marker = raw[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = int.from_bytes(raw[i + 5:i + 7], "big"), int.from_bytes(raw[i + 7:i + 9], "big")
                break
            i += 2 + int.from_bytes(raw[i + 2:i + 4], "big")
    if w <= 0 or h <= 0:
        return cap
    return max(1, min(cap, int(w * h / 750)))


def parse_time(ts):
    if not isinstance(ts, str):
        return None
    try:
        when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def preview(content, size=42):
    """A short, inert preview of a message: no app notes, no Markdown, links or HTML."""
    if isinstance(content, list):
        content = " ".join(str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text")
    text = NOTE_RE.sub(" ", content if isinstance(content, str) else "")
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r"(?i)\b(?:javascript|vbscript|data|file):", " ", text)
    text = re.sub(r"(?i)\b(?:https?://|www\.)\S+|\b[\w.-]+\.(?:com|net|org|io|ai|dev|app|co|fi|in|example)\b\S*", "[link]", text)
    text = re.sub(r"[^\w\s/.,:?!'()@#%+=\[\]-]", " ", text, flags=re.U).replace("[", "").replace("]", "")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "(attachment or empty)"
    return text if len(text) <= size else text[:size - 1].rstrip() + "…"


def context_of(u):
    return u["input_tokens"] + u["cache_read_input_tokens"] + u["cache_creation_input_tokens"]


def is_prompt(row, msg):
    """A message the person sent, as opposed to a tool result or a system note."""
    if row.get("type") != "user" or row.get("isMeta") or row.get("isSidechain"):
        return False
    origin = row.get("origin")
    if isinstance(origin, dict):
        return origin.get("kind") == "human"
    content = msg.get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return False
        content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return isinstance(content, str) and not content.startswith("[Request interrupted")


def effective_of(u):
    return (u["input_tokens"] + 0.1 * u["cache_read_input_tokens"]
            + 2 * u["cache_creation_input_tokens"] + 5 * u["output_tokens"])


def last_message(st):
    """Effective tokens of the message before the current one (the current one is /tokens itself)."""
    prev = st.turn - 1
    if prev < 1:
        return None
    calls = [u for k, u in st.calls.items() if st.turn_of.get(k) == prev]
    return sum(effective_of(u) for u in calls) if calls else None


class Stats:
    def __init__(self):
        self.calls = {}        # one usage per model call
        self.last = None       # usage of the main thread's latest call
        self.tools = {}        # tool_use id -> tool name
        self.first = None      # earliest timestamp
        self.latest = None     # latest timestamp
        self.cost = None       # USD per effective token, from the latest cost snapshot
        self.turn = 0          # number of messages the person has sent so far
        self.turn_of = {}      # model call -> the message it answered
        self.models = {}       # model call -> the model that answered it
        self.call_time = {}    # model call -> when it was answered
        self.prompts = []      # (preview, time) of each message the person sent
        self.tool_names = {}   # tool_use id -> tool name, to label tool results
        self.parts = {}        # context category -> estimated tokens (main thread, since any compaction)
        self.items = []        # (label, estimated tokens) of single large context items
        self.first_key = None  # the first main-thread call
        self.parts_at_first = None
        self.cards = 0         # cards drawn with the visual tool in this chat

    def read(self, path, main=False):
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            return
        with fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if main and row.get("type") == "cost-state" and row.get("totalCostUSD"):
                    # The session writes a cost snapshot now and then. Keep its
                    # price per effective token, to price the current totals.
                    eff = 0.0
                    for mu in (row.get("modelUsage") or {}).values():
                        if isinstance(mu, dict):
                            eff += (num(mu.get("inputTokens")) + 0.1 * num(mu.get("cacheReadInputTokens"))
                                    + 2 * num(mu.get("cacheCreationInputTokens")) + 5 * num(mu.get("outputTokens")))
                    usd = row["totalCostUSD"]
                    if eff > 0 and isinstance(usd, (int, float)) and math.isfinite(usd) and usd > 0:
                        self.cost = usd / eff
                ts = row.get("timestamp")
                if isinstance(ts, str):
                    try:
                        when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if when.tzinfo is None:
                            when = when.replace(tzinfo=timezone.utc)
                        self.first = when if self.first is None or when < self.first else self.first
                        self.latest = when if self.latest is None or when > self.latest else self.latest
                    except (ValueError, TypeError):
                        pass
                msg = row.get("message")
                if not isinstance(msg, dict):
                    continue
                if main and row.get("subtype") == "compact_boundary":
                    self.parts, self.items = {}, []
                if main and is_prompt(row, msg):
                    self.turn += 1
                    self.prompts.append((preview(msg.get("content")), parse_time(row.get("timestamp"))))
                if main and not row.get("isSidechain"):
                    self.add_context(row, msg)
                content = msg.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tid = str(block.get("id") or id(block))
                            if tid not in self.tools and str(block.get("name") or "").endswith("show_widget"):
                                self.cards += 1
                            self.tools[tid] = str(block.get("name") or "tool")
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                # One API response is stored as several rows (one per content
                # block), each repeating the same usage: count it once.
                key = str(msg.get("id") or row.get("uuid") or len(self.calls))
                usage = clean_usage(usage)
                if key not in self.calls and not any(usage[k] for k in KEYS):
                    continue
                if key not in self.calls:
                    self.call_time[key] = parse_time(row.get("timestamp"))
                # The app can make helper calls that report no tokens: the chat starts at the first real one.
                if main and not row.get("isSidechain") and self.first_key is None and context_of(usage) > 0:
                    self.first_key = key
                    self.parts_at_first = sum(self.parts.values())
                self.calls[key] = usage
                self.models[key] = str(msg.get("model") or "")
                self.turn_of.setdefault(key, self.turn)
                if not row.get("isSidechain"):
                    self.last = usage

    def add_context(self, row, msg):
        """Estimate how much each kind of content adds to the context the next calls re-read."""
        role = msg.get("role") or row.get("type")
        content = msg.get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        if not isinstance(blocks, list):
            return
        for b in blocks:
            if not isinstance(b, dict):
                continue
            kind = b.get("type")
            if role == "assistant":
                if kind == "tool_use":
                    self.tool_names[str(b.get("id"))] = str(b.get("name") or "tool")
                    self.add("Claude's replies and tool calls", est_text(json.dumps(b.get("input"), ensure_ascii=False)))
                elif kind == "text":
                    self.add("Claude's replies and tool calls", est_text(b.get("text")))
            elif kind == "text":
                text = str(b.get("text") or "")
                if row.get("isMeta") and "Base directory for this skill" in text:
                    self.add("Skill instructions", est_text(text))
                    continue
                notes = sum(len(m) for m in NOTE_RE.findall(text))
                self.add("App and system notes", est_chars(notes))
                self.add("App and system notes" if row.get("isMeta") else "Your messages",
                         est_chars(len(text) - notes))
            elif kind == "image":
                self.add("Images you attached", image_tokens(b.get("source")))
            elif kind == "tool_result":
                name = tool_label(self.tool_names.get(str(b.get("tool_use_id")), "tool"))
                inner = b.get("content")
                inner = [{"type": "text", "text": inner}] if isinstance(inner, str) else inner
                if not isinstance(inner, list):
                    continue
                for ib in inner:
                    if not isinstance(ib, dict):
                        continue
                    if ib.get("type") == "image":
                        n = image_tokens(ib.get("source"))
                        self.add("Screenshots and images from tools", n)
                        self.items.append((f"Screenshot or image from {name}", n))
                    elif ib.get("type") == "text":
                        n = est_text(ib.get("text"))
                        self.add(f"Tool output: {name}", n)
                        self.items.append((f"Output of {name}", n))

    def add(self, category, n):
        if n > 0:
            self.parts[category] = self.parts.get(category, 0) + n


# API list prices in USD per million tokens:
# (input, 5-minute cache write, 1-hour cache write, cache read, output).
# From https://platform.claude.com/docs/en/about-claude/pricing (October 2026).
PRICES = {
    "fable-5-1": (10, 12.5, 20, 0.25, 50),
    "mythos-5-1": (10, 12.5, 20, 0.25, 50),
    "opus-5-5": (4, 5, 8, 0.20, 20),
    "sonnet-5-5": (2, 2.5, 4, 0.10, 10),
    "haiku-5-5": (0.10, 0.125, 0.20, 0.01, 0.50),       # prompts up to 100K tokens
    "haiku-5-5-long": (0.50, 0.625, 1, 0.05, 2.50),     # prompts over 100K tokens
    "fable-5": (10, 12.5, 20, 1, 50),
    "mythos-5": (10, 12.5, 20, 1, 50),
    "opus-5": (5, 6.25, 10, 0.50, 25),
    "opus-4-8": (5, 6.25, 10, 0.50, 25),
    "opus-4-7": (5, 6.25, 10, 0.50, 25),
    "opus-4-6": (5, 6.25, 10, 0.50, 25),
    "opus-4-5": (5, 6.25, 10, 0.50, 25),
    "opus-4-1": (15, 18.75, 30, 1.50, 75),
    "opus-4": (15, 18.75, 30, 1.50, 75),
    "sonnet-5": (2, 2.5, 4, 0.20, 10),
    "sonnet-4-6": (3, 3.75, 6, 0.30, 15),
    "sonnet-4-5": (3, 3.75, 6, 0.30, 15),
    "sonnet-4": (3, 3.75, 6, 0.30, 15),
    "haiku-4-5": (1, 1.25, 2, 0.10, 5),
}


def call_cost(model, u):
    """USD for one model call at list prices, or None for a model not in the table."""
    name = re.sub(r"-\d{8}$", "", re.sub(r"^claude-", "", model.lower()))
    read = u["cache_read_input_tokens"]
    write = u["cache_creation_input_tokens"]
    plain = u["input_tokens"]
    out = u["output_tokens"]
    if name == "haiku-5-5" and plain + read + write > 100_000:
        name = "haiku-5-5-long"
    price = PRICES.get(name)
    if price is None:
        return None
    w1h = min(u["cache_1h"], write) if u["cache_1h"] is not None else 0
    w5m = write - w1h
    p_in, p_w5, p_w1h, p_read, p_out = price
    return (plain * p_in + w5m * p_w5 + w1h * p_w1h + read * p_read + out * p_out) / 1_000_000


def priced(st, key, u):
    """One call's cost: list price for its model, else the session's own cost rate.
    0 for a call with no tokens; None when there is no way to price it."""
    c = call_cost(st.models.get(key, ""), u)
    if c is not None:
        return c
    if not effective_of(u):
        return 0.0
    return effective_of(u) * st.cost if st.cost is not None else None


def api_cost(st):
    """The chat's cost at API list prices, or None when some call cannot be priced."""
    total = 0.0
    for key, u in st.calls.items():
        c = priced(st, key, u)
        if c is None:
            return None
        total += c
    return total


def pct(part, whole):
    if not whole:
        return "0%"
    p = 100 * part / whole
    return "&lt;1%" if 0 < p < 1 else f"{p:.0f}%"


def pct_text(part, whole):
    return pct(part, whole).replace("&lt;", "<")


def esc(text):
    return html.escape(str(text), quote=True)


# ---- Cards (HTML for the chat's inline visual tool) ----------------------------
#
# One small style block and short class names keep the card a few KB, so the
# script's output and the copy Claude passes to the visual tool cost less.
# Each usage type keeps one color everywhere: c0 input, c1 cache read,
# c2 cache write, c3 output.

CSS_BASE = (
    ".tm{background:var(--surface-2);border:.5px solid var(--border);border-radius:12px;padding:1.1rem 1.25rem;font-variant-numeric:tabular-nums}"
    ".tm .h{display:flex;justify-content:space-between;align-items:center;gap:12px}"
    ".tm .i{display:flex;align-items:center;gap:6px}.tm .ti{font-size:15px}"
    ".tm .s{font-size:13px;color:var(--text-secondary)}.tm .m{font-size:12px;color:var(--text-muted)}"
    ".tm .top{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;margin:8px 0 12px}"
    ".tm .big{font-family:var(--font-voice);line-height:1.1}.tm .v{font-size:20px;font-weight:500}"
    ".tm .bar{display:flex;gap:2px;height:12px;border-radius:6px;overflow:hidden}.tm .bar b{min-width:4px}"
    ".tm .sq{width:9px;height:9px;border-radius:2px;flex:none}"
    ".tm .c0{background:var(--series-1,#2a78d6)}.tm .c1{background:var(--series-2,#eb6834)}"
    ".tm .c2{background:var(--series-3,#1baf7a)}.tm .c3{background:var(--series-4,#eda100)}"
    ".tm .g{display:grid;gap:10px;margin-top:12px}.tm .t{background:var(--surface-1);border-radius:var(--radius);padding:12px 14px;min-width:0}"
    ".tm .sec{margin-top:12px;padding-top:10px;border-top:.5px solid var(--border)}"
)
CSS_DETAILED = (
    ".tm .p{font-size:12px;background:var(--bg-accent);color:var(--text-accent);padding:3px 10px;border-radius:var(--radius)}"
    ".tm .r{display:grid;grid-template-columns:12px minmax(0,1fr) auto 40px;gap:10px;align-items:center;padding:9px 0;border-top:.5px solid var(--border);font-size:14px}"
    ".tm .tl{display:grid;grid-template-columns:minmax(0,150px) minmax(0,1fr) 32px;gap:10px;align-items:center;padding:4px 0;font-size:13px}"
    ".tm .tl b{height:8px;border-radius:0 4px 4px 0}.tm .nm{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
)
CSS_SUMMARY = ".tm .lg{display:inline-flex;align-items:center;gap:5px;margin-right:14px}"


def style(extra):
    return f"<style>{CSS_BASE}{extra}</style>"


def icon(name):
    return f'<i class="ti {name}" aria-hidden="true"></i>'


def by_share(rows):
    """(color index, row) pairs, largest effective share first."""
    return sorted(enumerate(rows), key=lambda r: -(r[1][1] * r[1][2]))


def share_bar(rows, effective):
    segs = "".join(f'<b class="c{i}" style="flex:{100 * n * w / effective:.2f}"></b>'
                   for i, (_, n, w, _) in by_share(rows) if effective and 100 * n * w / effective >= 0.5)
    return f'<div class="bar">{segs}</div>'


def head(rows, effective, n_calls, cost, size, pill):
    top = max(rows, key=lambda r: r[1] * r[2])[0].replace(" (uncached)", "").lower()
    right = (f'<div style="text-align:right"><div class="v">${cost:.2f}</div>'
             f'<div class="m">API cost, est.</div></div>') if cost is not None else ""
    return (
        f'<h2 class="sr-only">Token usage in this chat: {fmt(effective)} effective tokens across {n_calls} model calls.</h2>'
        f'<div class="h s"><span class="i">{icon("ti-chart-bar")}Token usage in this chat</span>'
        + (f'<span class="p">Most from {esc(top)}</span>' if pill else "") + '</div>'
        f'<div class="top"><span><span class="big" style="font-size:{size}px">{fmt(effective)}</span> '
        f'<span class="s">effective tokens</span></span>{right}</div>')


def tiles(items, columns, plain=False):
    style = ' style="background:none;padding:0"' if plain else ""
    cells = "".join(f'<div class="t"{style}><div class="m i">{icon(ic)}{label}</div><div class="v">{value}</div></div>'
                    for label, value, ic in items)
    top = "margin-top:0;" if plain else ""
    return f'<div class="g" style="{top}grid-template-columns:{columns}">{cells}</div>'


def hit_rate(rows):
    prompt_in = rows[0][1] + rows[1][1] + rows[2][1]
    return f"{100 * rows[1][1] / prompt_in:.0f}%" if prompt_in else "0%"


def card_detailed(st, rows, effective, ctx):
    """Detailed view: the summary plus a row per usage type, six tiles and the most used tools."""
    n_calls = len(st.calls)
    thinking = sum(u["thinking_tokens"] for u in st.calls.values())
    cost = api_cost(st)
    lines = "".join(
        f'<div class="r"><span class="sq c{i}"></span><span>{name}<span class="m"> &middot; {fmt(n)} {label}'
        + (f" &middot; {fmt(thinking)} thinking" if name == "Output" and thinking else "")
        + f'</span></span><span>{fmt(n * w)}</span><span class="s" style="text-align:right">{pct(n * w, effective)}</span></div>'
        for i, (name, n, w, label) in enumerate(rows))
    duration = (st.latest - st.first).total_seconds() if st.first and st.latest else None
    six = [("Context, last call", fmt(ctx) if ctx is not None else "n/a", "ti-stack-2"),
           ("Model calls", str(n_calls), "ti-sparkles"),
           ("Cache hit rate", hit_rate(rows), "ti-bolt"),
           ("Thinking tokens", fmt(thinking), "ti-bulb"),
           ("Chat duration", fmt_duration(duration) if duration is not None else "n/a", "ti-clock"),
           ("Tool calls", str(len(st.tools)), "ti-tool")]

    counts = {}
    for name in st.tools.values():
        label = tool_label(name)
        counts[label] = counts.get(label, 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:5]
    tools = ""
    if top:
        peak = top[0][1]
        items = "".join(
            f'<div class="tl"><span class="nm">{esc(name)}</span><b class="c0" style="width:{max(4, 100 * n / peak):.0f}%"></b>'
            f'<span class="s" style="text-align:right">{n}</span></div>'
            for name, n in top)
        tools = f'<div class="sec"><div class="s i" style="margin-bottom:4px">{icon("ti-tools")}Most used tools</div>{items}</div>'

    note = " API cost is at API list prices, not your plan's bill." if cost is not None else ""
    return (style(CSS_DETAILED) + '<div class="tm">' + head(rows, effective, n_calls, cost, 48, True) + share_bar(rows, effective)
            + f'<div style="margin-top:8px">{lines}</div>' + tiles(six, "repeat(3,minmax(0,1fr))") + tools
            + f'<div class="m" style="margin-top:12px">Effective tokens weight cache reads at &times;0.1, '
              f'cache writes at &times;2 and output at &times;5.{note}</div></div>')


def card_summary(st, rows, effective, ctx):
    """Summary view: total and cost, one share bar with an inline legend, last message and context."""
    cost = api_cost(st)
    legend = "".join(f'<span class="lg"><span class="sq c{i}"></span>{name.replace(" (uncached)", "")} {pct(n * w, effective)}</span>'
                     for i, (name, n, w, _) in by_share(rows))
    stats = []
    prev = last_message(st)
    if prev is not None:
        stats.append(("Last message", fmt(prev), "ti-message-2"))
    if ctx is not None:
        stats.append(("Context, last call", fmt(ctx), "ti-stack-2"))
    bottom = (f'<div class="sec" style="margin-top:12px">{tiles(stats, f"repeat({len(stats)},minmax(0,1fr))", plain=True)}</div>'
              if stats else "")
    return (style(CSS_SUMMARY) + '<div class="tm">' + head(rows, effective, len(st.calls), cost, 40, False) + share_bar(rows, effective)
            + f'<div class="m" style="margin-top:8px;line-height:1.8;color:var(--text-secondary)">{legend}</div>'
            + bottom + '</div>')


# ---- Lite view (Markdown text) ---------------------------------------------------

SQUARES = ["🟦", "🟧", "🟩", "🟨"]   # same colors as the cards: input, cache read, cache write, output


def square_bar(i, share):
    """The row's own colored square, one per 10% (rounded). Emoji render the same in light and dark mode."""
    return SQUARES[i] * int(round(share / 10))


def lite(st, rows, effective, ctx):
    """Lite view: Markdown only, so no card has to be drawn."""
    cost = api_cost(st)
    head_line = f"**{fmt(effective)}** effective tokens"
    if cost is not None:
        head_line += f"  ·  **${cost:.2f}** API cost (est.)"
    lines = ["### 🔢 Token usage · this chat", "", head_line, "",
             "| Type | Effective | Share |", "| :--- | ---: | :--- |"]
    for i, (name, n, w, _) in by_share(rows):
        share = (100 * n * w / effective) if effective else 0
        bar = square_bar(i, share) or (SQUARES[i] if share > 0 else "")
        lines.append(f"| {name.replace(' (uncached)', '')} | {fmt(n * w)} | {bar} {pct_text(n * w, effective)} |")
    lines.append(f"| **Total** | **{fmt(effective)}** | **100%** |")
    tail = []
    prev = last_message(st)
    if prev is not None:
        tail.append(f"💬 Last message **{fmt(prev)}**")
    if ctx is not None:
        tail.append(f"📚 Context **{fmt(ctx)}**")
    if tail:
        lines += ["", "  ·  ".join(tail)]
    if ctx:
        level, label, _, _ = health_info(st, ctx)
        lines += ["", f"{level} {label}  ·  more: `/tokens history`, `where`, `health`, `tips`, `compare`"]
    return "\n".join(lines)


# ---- Insight views (Markdown text) --------------------------------------------------

MODEL_NAMES = {
    "fable-5-1": "Fable 5.1", "mythos-5-1": "Mythos 5.1", "opus-5-5": "Opus 5.5", "sonnet-5-5": "Sonnet 5.5",
    "haiku-5-5": "Haiku 5.5", "opus-5": "Opus 5", "sonnet-5": "Sonnet 5", "opus-4-8": "Opus 4.8",
    "sonnet-4-6": "Sonnet 4.6", "haiku-4-5": "Haiku 4.5",
}


def model_key(model):
    return re.sub(r"-\d{8}$", "", re.sub(r"^claude-", "", str(model).lower()))


def main_model(st):
    counts = {}
    for key in st.calls:
        k = model_key(st.models.get(key, ""))
        if k in PRICES:
            counts[k] = counts.get(k, 0) + 1
    return max(counts, key=counts.get) if counts else None


def ago(when, now):
    if when is None:
        return ""
    sec = max(0, int((now - when).total_seconds()))
    if sec < 60:
        return "just now"
    if sec < 3600:
        return f"{sec // 60}m ago"
    if sec < 86400:
        return f"{sec // 3600}h ago"
    return f"{sec // 86400}d ago"


def money(x):
    return f"${x:.2f}" if x >= 0.01 else "<$0.01"


def turn_totals(st):
    """Per finished message: (calls, effective tokens, cost or None). The running /tokens message is left out."""
    out = {}
    for key, u in st.calls.items():
        t = st.turn_of.get(key, 0)
        if t < 1 or t >= st.turn:
            continue
        calls, eff, cost = out.get(t, (0, 0.0, 0.0))
        c = priced(st, key, u)
        out[t] = (calls + 1, eff + effective_of(u), None if cost is None or c is None else cost + c)
    return out


def history(st):
    now = datetime.now(timezone.utc)
    totals = turn_totals(st)
    turns = sorted(totals)[-10:]
    if not turns:
        return "### 🕘 Message history\n\nNo finished messages to show yet. Try again after your next message."
    peak = max(totals[t][1] for t in turns)
    lines = ["### 🕘 Your last messages", "",
             "| # | Message | When | Calls | Effective | Cost |", "| ---: | :--- | :--- | ---: | ---: | ---: |"]
    for t in turns:
        calls, eff, cost = totals[t]
        text, when = st.prompts[t - 1] if t - 1 < len(st.prompts) else ("", None)
        mark = " 🔺" if eff == peak and len(turns) > 1 else ""
        text = text or "(not recorded)"
        lines.append(f"| {t} | {text} | {ago(when, now)} | {calls} | **{fmt(eff)}**{mark} | "
                     f"{money(cost) if cost is not None else 'n/a'} |")
    lines += ["", ("🔺 the most expensive of these. " if len(turns) > 1 else "")
              + "Your current `/tokens` message is still running, so it is not listed."]
    return "\n".join(lines)


def setup_size(st, ctx):
    """Tokens the chat starts with before any message: the first call's context less what came before it."""
    # A call can be written several times while it streams; its last copy has the full usage.
    first = st.calls.get(st.first_key)
    first_ctx = context_of(first) if first else 0
    return min(max(0, first_ctx - (st.parts_at_first or 0)), ctx or 0)


def composition(st, ctx):
    """The context of the last call split into setup and estimated parts, scaled to the real size."""
    setup = setup_size(st, ctx)
    parts = dict(st.parts)
    total_est = sum(parts.values())
    conv = max(0, (ctx or 0) - setup)
    scale = conv / total_est if total_est and conv else 0
    rows = [("Chat setup (system prompt, tools, skill list)", setup)]
    rows += [(k, v * scale) for k, v in parts.items()]
    return sorted((r for r in rows if r[1] >= 1), key=lambda r: -r[1]), setup, scale


def where(st, ctx):
    if not ctx:
        return "### 🧭 Where it goes\n\nNothing to show yet."
    rows, _, scale = composition(st, ctx)
    model = main_model(st)
    read_price = PRICES[model][3] if model else None
    lines = ["### 🧭 What every call re-reads", "",
             f"Each model call re-reads the whole context: **{fmt(ctx)}** tokens now"
             + (f", about **{money(ctx * read_price / 1e6)}** per call at cache-read prices." if read_price else "."), "",
             "| Part of the context | Tokens | Share |", "| :--- | ---: | :--- |"]
    shown = rows[:8]
    rest = sum(v for _, v in rows[8:])
    if rest:
        shown.append(("Everything else", rest))
    for name, v in shown:
        share = 100 * v / ctx
        lines.append(f"| {name} | {fmt(v)} | {'▰' * max(1, round(share / 10)) if share >= 1 else ''} {pct_text(v, ctx)} |")
    big = sorted(st.items, key=lambda it: -it[1])[:3]
    if big and scale:
        lines += ["", "**Biggest single items:** " + "; ".join(f"{n} ({fmt(v * scale)})" for n, v in big)]
    lines += ["", "_Parts are estimated from the text and image sizes in this chat and scaled to the real context size._"]
    return "\n".join(lines)


def health_info(st, ctx):
    setup = setup_size(st, ctx)
    grown = max(0, (ctx or 0) - setup)
    if grown < 100_000:
        level, label = "🟢", "Light"
    elif grown < 250_000:
        level, label = "🟡", "Getting heavy"
    else:
        level, label = "🔴", "Heavy"
    return level, label, setup, grown


def cache_ttl(st):
    """Minutes the prompt cache lives: 60 when recent writes used the 1-hour cache, else 5."""
    recent = sorted(st.call_time.items(), key=lambda kv: kv[1] or datetime.min.replace(tzinfo=timezone.utc))[-20:]
    for key, _ in reversed(recent):
        u = st.calls.get(key)
        if u and u["cache_creation_input_tokens"]:
            return 60 if u["cache_1h"] else 5
    return 60


def health(st, ctx):
    if not ctx:
        return "### 🩺 Chat health\n\nNothing to show yet."
    level, label, setup, grown = health_info(st, ctx)
    model = main_model(st)
    totals = turn_totals(st)
    recent = [totals[t][1] for t in sorted(totals)[-3:]]
    lines = ["### 🩺 Chat health", "", f"**{level} {label}**", "",
             f"- Context now: **{fmt(ctx)}** tokens ({fmt(setup)} chat setup + **{fmt(grown)}** from this conversation)"]
    if recent:
        lines.append(f"- Your recent messages cost about **{fmt(sum(recent) / len(recent))}** effective tokens each")
    ttl = cache_ttl(st)
    if model:
        p_in, p_w5, p_w1h, p_read, _ = PRICES[model]
        warm = ctx * p_read / 1e6
        cold = ctx * (p_w1h if ttl == 60 else p_w5) / 1e6
        lines.append(f"- Every call re-reads it: about **{money(warm)}** while the cache is warm")
        lines += ["", f"**⏱️ Cache timer:** the cache lasts about **{ttl} minutes** after each reply. "
                  f"Pause longer and your next message writes the whole context again: about **{money(cold)}** "
                  f"instead of {money(warm)} for that first call."]
    lines.append("")
    if level == "🔴":
        lines.append("**Advice:** start a new chat. Send `/tokens handoff` first to get a short note to paste there.")
    elif level == "🟡":
        lines.append("**Advice:** fine for now. When you switch to a new topic, start a new chat (`/tokens handoff` helps).")
    else:
        lines.append("**Advice:** nothing to do.")
    return "\n".join(lines)


def tips(st, rows, effective, ctx):
    found = []
    if ctx:
        level, _, _, grown = health_info(st, ctx)
        if level != "🟢":
            found.append(("Start a new chat for the next topic",
                          f"this conversation adds {fmt(grown)} tokens that every call re-reads. `/tokens handoff` writes a note to carry over."))
        comp, _, _ = composition(st, ctx)
        shots = sum(v for n, v in comp if n == "Screenshots and images from tools")
        if shots / ctx > 0.10:
            found.append(("Read pages as text instead of screenshots",
                          f"tool screenshots fill {pct_text(shots, ctx)} of the context ({fmt(shots)}); page text is usually far smaller."))
        big = [v for _, v in st.items if v > 8_000]
        if big:
            found.append(("Ask for trimmed tool output",
                          f"{len(big)} tool result(s) were over 8k tokens each; asking for filtered or shortened output keeps them out of the context."))
    reads, writes = rows[1][1], rows[2][1]
    if writes and reads / max(1, reads + writes + rows[0][1]) < 0.85 and len(st.calls) > 5:
        found.append(("Keep pauses under the cache timer",
                      f"only {hit_rate(rows)} of input came from the cache; after a long pause the whole context is written again at 2x."))
    if st.cards >= 3:
        found.append(("Use `/tokens lite` for quick checks",
                      f"{st.cards} cards were drawn in this chat; each card costs far more than the text view."))
    out_tokens = rows[3][1]
    thinking = sum(u["thinking_tokens"] for u in st.calls.values())
    if out_tokens and thinking / out_tokens > 0.5 and out_tokens > 5_000:
        found.append(("Lower the effort setting for simple chats",
                      f"thinking is {pct_text(thinking, out_tokens)} of all output ({fmt(thinking)} tokens)."))
    totals = turn_totals(st)
    if totals:
        avg_calls = sum(c for c, _, _ in totals.values()) / len(totals)
        if avg_calls > 10:
            found.append(("Give fuller instructions up front",
                          f"messages take {avg_calls:.0f} model calls on average; each call re-reads the context."))
    if effective and rows[3][1] * 5 / effective > 0.25:
        found.append(("Ask for shorter answers",
                      f"Claude's output is {pct_text(rows[3][1] * 5, effective)} of the effective total (output counts 5x)."))
    lines = ["### 💡 Ways to spend less", ""]
    if not found:
        lines.append("Nothing stands out: this chat is already lean.")
    for i, (title, why) in enumerate(found[:5], 1):
        lines.append(f"{i}. **{title}**: {why}")
    return "\n".join(lines)


def compare(st):
    current = main_model(st)
    options = ["fable-5-1", "opus-5-5", "sonnet-5-5", "haiku-5-5", "opus-5", "sonnet-4-6"]
    if current and current not in options:
        options.insert(0, current)
    costs = []
    for m in options:
        total = 0.0
        for u in st.calls.values():
            total += call_cost(m, u) or 0
        costs.append((m, total))
    base = next((c for m, c in costs if m == current), None)
    lines = ["### ⚖️ This chat on other models", "",
             "| Model | Est. cost | vs now |", "| :--- | ---: | ---: |"]
    for m, c in sorted(costs, key=lambda mc: -mc[1]):
        name = MODEL_NAMES.get(m, m)
        if m == current:
            name = f"**{name} (used)**"
        vs = "" if not base or m == current else (f"{c / base:.1f}x" if c >= base else f"{100 * (1 - c / base):.0f}% less")
        lines.append(f"| {name} | {money(c)} | {vs} |")
    lines += ["", "_Same token counts priced at each model's list prices. Real counts and answer quality differ by model._"]
    return "\n".join(lines)


def main():
    arg = " ".join(sys.argv[1:]).strip().lower() or "lite"
    if arg not in ("detailed", "summary", "lite", "history", "where", "health", "tips", "compare"):
        print(HELP if arg == "help" else f"Unknown option.\n\n{HELP}")
        return

    main_file = current_transcript()
    if not main_file:
        print("No session transcript found, so there is nothing to count yet.")
        return

    st = Stats()
    st.read(main_file, main=True)
    session_dir = os.path.splitext(main_file)[0]
    for sub in glob.glob(os.path.join(glob.escape(session_dir), "**", "*.jsonl"), recursive=True):
        st.read(sub)

    if not st.calls:
        print("Nothing counted yet; try again after the next reply.")
        return

    t = {k: sum(u[k] for u in st.calls.values()) for k in KEYS}
    rows = [
        ("Input (uncached)", t["input_tokens"], 1, "×1"),
        ("Cache read", t["cache_read_input_tokens"], 0.1, "×0.1"),
        ("Cache write", t["cache_creation_input_tokens"], 2, "×2"),
        ("Output", t["output_tokens"], 5, "×5"),
    ]
    effective = sum(n * w for _, n, w, _ in rows)
    ctx = None
    if st.last:
        ctx = context_of(st.last)
    views = {"history": lambda: history(st), "where": lambda: where(st, ctx), "health": lambda: health(st, ctx),
             "tips": lambda: tips(st, rows, effective, ctx), "compare": lambda: compare(st)}
    if arg in views:
        print(views[arg]())
    elif arg == "detailed":
        print(card_detailed(st, rows, effective, ctx))
    elif arg == "summary":
        print(card_summary(st, rows, effective, ctx))
    else:
        print(lite(st, rows, effective, ctx))


if __name__ == "__main__":
    try:
        main()
    except Exception as err:  # never print transcript content or paths back into the chat
        print(f"Couldn't read the token count ({type(err).__name__}).")
