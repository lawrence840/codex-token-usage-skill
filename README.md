# Codex Token Usage Skill

**English** | [中文](README.zh-CN.md)

> This fork extends the upstream project with detailed local usage profiling. See [What this fork adds](#what-this-fork-adds).

**See your Codex token usage, from the total down to every day, model, project and session.**

Analyze local Codex Desktop / CLI session logs, generate an offline HTML dashboard, and open it in your system's default external browser. After installation, just ask Codex: “Summarize this week's usage.”

## Dashboard preview

Track total and net tokens, cache hit rate, sessions and daily averages. The offline dashboard also attributes usage to models, reasoning efforts, clients, projects and sessions, and flags explainable local diagnostics for unusually large contexts.

![Codex usage dashboard with summary cards, daily trends and token breakdown](docs/images/usage-dashboard.png)

Sort daily details by date or total usage, hover for exact counts, and export a CSV for further analysis.

![Daily details with peak day, cache reuse, busiest week and daily token counts](docs/images/daily-details.png)

*Screenshots show a local report snapshot with the Chinese interface. Your results depend on your logs, date range and timezone; English labels are available with `--language en`.*

## Install and use

Install into your Codex environment with Skills CLI:

```bash
npx skills add https://github.com/lawrence840/codex-token-usage-skill --skill codex-token-usage
```

Then ask Codex in plain language:

```text
Summarize my Codex token usage for this week.
```

```text
Show my last 30 days of usage, including daily counts and cache hit rate.
```

```text
Generate an HTML dashboard of my usage for August 2026.
```

```text
Which model and session used the most tokens this week?
```

```text
Check for long-context or heavily cached Codex sessions.
```

The skill resolves the dates, runs the report script, generates the fixed HTML template and requests an external browser to open it.

| Section | Contents |
| --- | --- |
| Overview | Total tokens, net tokens, cache hit rate, active sessions and daily average |
| Daily trend | Stacked bars, reported-total line, total/net switch and keyboard-accessible day selection |
| Token breakdown | Uncached input, cached input, output and any difference from the reported total |
| Highlights | Peak day, cache reuse and busiest week |
| Daily details | All dates including inactive days, sorting, exact-value tooltips and CSV export |

Compact token values use **亿 (100 million), 千万 (10 million) and 百万 (1 million)** in both interface languages. Values below one million are shown in full. Daily tables use the same units; tooltips and CSV retain exact counts.

## What this fork adds

This fork keeps the upstream token-accounting semantics and extends the local, offline report with:

- Model and reasoning-effort attribution.
- One-row-per-session analysis, including dominant model, average tokens per event and session titles when locally available.
- Project-basename, client and source summaries.
- Explainable local diagnostics for dominant sessions, large context per event, heavy cached context and long-lived high-volume sessions.
- A `--privacy strict` mode that removes session titles, project, client and source attribution from the generated report.
- `--machine-name` JSON metadata and `--top-sessions` control for Markdown output.

Diagnostics are local heuristics. They are not OpenAI billing, quota, account-usage or policy thresholds.

## Run the script directly

Requires Python 3.9+. IANA timezones require a system timezone database or the `tzdata` package. Run these examples from the repository root.

Generate and open a dashboard for the last 30 calendar days:

```bash
python -B skills/codex-token-usage/scripts/codex_token_usage.py --days 30 --timezone Asia/Shanghai --language en
```

The default output is `output/token-usage-<start>-<end>.html` under the working directory.

| Option | Purpose | Example |
| --- | --- | --- |
| `--days` | Last N calendar days ending today or on `--end` | `--days 7` |
| `--month` | Calendar month, clipped to today for the current month | `--month 2026-08` |
| `--start` / `--end` | Inclusive date range | `--start 2026-09-01 --end 2026-09-08` |
| `--timezone` | Reporting timezone | `--timezone Asia/Shanghai` |
| `--codex-home` | Log directory; defaults to `CODEX_HOME`, then `~/.codex` | `--codex-home ~/.codex` |
| `--output` | Output file, supported by all formats | `--output output/usage.html` |
| `--no-open` | Generate HTML without launching a browser | `--no-open` |
| `--format` | HTML (default), Markdown or JSON | `--format json` |
| `--language` | Chinese (default) or English labels | `--language en` |
| `--machine-name` | Local machine label stored in the JSON schema | `--machine-name HOME-PC` |
| `--privacy` | `standard` includes local titles/basenames; `strict` hides them | `--privacy strict` |
| `--top-sessions` | Session rows included in Markdown | `--top-sessions 20` |

“This week” means Monday through today. When using the script directly, pass those dates with `--start` and `--end`; `--days 7` is a rolling seven-day window.

Generate a file for automation:

```bash
python -B skills/codex-token-usage/scripts/codex_token_usage.py --days 30 --no-open --output output/usage.html
```

Use text or machine-readable output:

```bash
python -B skills/codex-token-usage/scripts/codex_token_usage.py --days 30 --format markdown
python -B skills/codex-token-usage/scripts/codex_token_usage.py --days 30 --format json
```

Markdown and JSON print to stdout by default and do not open a browser. **Scripts that relied on the old default Markdown output must add `--format markdown` when upgrading.** If an existing Codex task retains old instructions, ask it to reread the installed codex-token-usage skill.

## How usage is counted

The script reads JSONL files in `sessions/` and `archived_sessions/`, deduplicates events by session, timestamp and selected usage fields, and sums `last_token_usage` from `token_count` events. It does not sum the cumulative `total_token_usage` on every event.

| Metric | Calculation |
| --- | --- |
| Total | Sum of `last_token_usage.total_tokens` |
| Input / cached input | Sum of `input_tokens` / `cached_input_tokens` |
| Output / reasoning output | Sum of `output_tokens` / `reasoning_output_tokens` |
| Uncached input | `Input − cached input` |
| Net usage | `Uncached input + output` |
| Cache hit rate | `Cached input ÷ input` |
| Daily average | `Total ÷ selected calendar days`, including inactive dates |

Cache is part of input, and reasoning is part of output; neither is stacked twice. Reported totals may differ from input plus output, and the dashboard states the difference. Weeks start on Monday and count only events in the selected range. Token event counts are not tool call counts.

These are statistics from locally readable logs, not account billing or live quota data. JSON includes summary, daily and weekly rows, peaks, parser diagnostics, model/effort/client/project/session attribution, timezone and generation time. A session remains one session even if it changes models or reasoning effort.

## Usage profiler

The parser keeps the JSONL state needed to attribute each `token_count` event: session metadata, the latest model and reasoning effort, and optional session titles from `session_index.jsonl`. It reports aggregate data only; the HTML never includes prompts, full paths, rollout paths or session UUIDs.

The dashboard adds model, session, client/source and project tables. Local diagnostics identify a dominant session, large average context, heavy cached context and long-lived high-volume sessions. They are transparent heuristics, not official billing, quota or policy thresholds.

## Local and offline

The report process does not upload logs, credentials or usage reports. HTML embeds aggregate data only, without prompts, session IDs or source log paths.

Every report uses the same template with embedded CSS, JavaScript and data. No server, build step or CDN is required; JavaScript must be enabled in the browser. Reports are snapshots and update when regenerated. If browser launch fails, the saved file can still be opened manually.

## Development checks

```bash
python -B skills/codex-token-usage/scripts/test_codex_token_usage.py
npx skills add . --list
```

Tests cover daily/summary consistency, inactive dates, timezones, archive deduplication, HTML export, default browser dispatch and `--no-open`. Browser launches are mocked in tests.

## License

[MIT](LICENSE)
