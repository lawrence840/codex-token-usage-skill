#!/usr/bin/env python3
"""Build private, offline Codex usage reports from local JSONL session logs."""
import argparse
import calendar
import contextlib
import io
import json
import os
import pathlib
import re
import socket
import sys
import time
import webbrowser
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Iterator
from zoneinfo import ZoneInfo

@dataclass
class TokenUsage:
    input: int = 0; cached_input: int = 0; output: int = 0; reasoning: int = 0; total: int = 0
    @property
    def fresh_input(self): return max(self.input - self.cached_input, 0)
    @property
    def net_usage(self): return self.fresh_input + self.output
    @property
    def cache_hit_rate(self): return self.cached_input / self.input if self.input else 0.0
    def add(self, other):
        self.input += other.input; self.cached_input += other.cached_input; self.output += other.output; self.reasoning += other.reasoning; self.total += other.total

@dataclass
class SessionMeta:
    session_id: str; originator: str | None = None; source: str | None = None; cwd: str | None = None
    @property
    def project(self): return pathlib.PurePath(self.cwd).name if self.cwd else None

@dataclass
class TurnContext:
    model: str | None = None; effort: str | None = None

@dataclass
class UsageEvent:
    session_id: str; timestamp: datetime; usage: TokenUsage; model: str | None = None; effort: str | None = None; originator: str | None = None; source: str | None = None; project: str | None = None; title: str | None = None
    @property
    def day(self): return self.timestamp.date()

@dataclass
class DiscoveryStats:
    files_discovered: int = 0; files_read: int = 0; files_skipped: int = 0; malformed_json_lines: int = 0

@dataclass
class SessionSummary:
    session_id: str; title: str | None; project: str | None; originator: str | None; source: str | None; events: int; usage: TokenUsage; first_event: datetime; last_event: datetime; dominant_model: str | None; dominant_model_share: float; dominant_effort: str | None; average_tokens_per_event: float; model_usage: dict[str, TokenUsage] = field(default_factory=dict); effort_usage: dict[str, TokenUsage] = field(default_factory=dict)

def parse_args():
    p = argparse.ArgumentParser(description="Summarize Codex token usage from local session JSONL logs.")
    p.add_argument("--codex-home", default=os.environ.get("CODEX_HOME") or str(pathlib.Path.home() / ".codex")); p.add_argument("--timezone", default=None); p.add_argument("--days", type=int, default=None); p.add_argument("--start", default=None); p.add_argument("--end", default=None); p.add_argument("--month", default=None); p.add_argument("--format", choices=["markdown", "json", "html"], default="html"); p.add_argument("--output", type=pathlib.Path); p.add_argument("--no-open", action="store_true"); p.add_argument("--language", choices=["zh", "en"], default="zh"); p.add_argument("--machine-name", default=None); p.add_argument("--privacy", choices=["standard", "strict"], default="standard"); p.add_argument("--top-sessions", type=int, default=10)
    return p.parse_args()

def local_today(tz): return datetime.now(tz).date()
def resolve_range(args, tz):
    if args.month:
        y, m = [int(x) for x in args.month.split("-", 1)]; start, end = date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1]); today = local_today(tz); return start, min(end, today) if start <= today <= end else end
    end = date.fromisoformat(args.end) if args.end else local_today(tz); return (date.fromisoformat(args.start) if args.start else end - timedelta(days=(args.days or 30)-1)), end
def session_id(path):
    found = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", path.name, re.I); return found.group(1) if found else path.stem
def normalize_source(value: Any):
    if value is None: return None
    if isinstance(value, str): return value
    if isinstance(value, dict): return "subagent:" + str(value["subagent"]) if value.get("subagent") else ("subagent" if "subagent" in value else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return str(value)
def load_session_titles(home, stats=None):
    out, path = {}, home / "session_index.jsonl"
    if not path.exists(): return out
    try:
        with path.open("r", encoding="utf-8", errors="replace") as h:
            for line in h:
                try: row = json.loads(line)
                except json.JSONDecodeError:
                    if stats: stats.malformed_json_lines += 1
                    continue
                if row.get("id") and row.get("thread_name"): out[str(row["id"])] = str(row["thread_name"])
    except OSError: pass
    return out
def discover_session_files(home, start=None, tz=None, stats=None):
    paths = [p for root in (home / "sessions", home / "archived_sessions") if root.exists() for p in root.rglob("*.jsonl")]
    if stats: stats.files_discovered = len(paths)
    return paths
@contextlib.contextmanager
def open_text_with_retry(path, attempts=5, delay_seconds=.1):
    last = None
    for attempt in range(attempts):
        try:
            h = path.open("r", encoding="utf-8", errors="replace")
            try: yield h
            finally: h.close()
            return
        except OSError as error:
            last = error
            if attempt + 1 < attempts: time.sleep(delay_seconds)
    raise last
def parse_session_meta(payload, fallback):
    vals = payload.get("meta") if isinstance(payload.get("meta"), dict) else payload
    return SessionMeta(str(vals.get("session_id") or vals.get("id") or fallback), vals.get("originator"), normalize_source(vals.get("source")), vals.get("cwd"))
def parse_turn_context(payload, previous): return TurnContext(payload.get("model") or previous.model, payload.get("effort") or payload.get("reasoning_effort") or previous.effort)
def number(value):
    try: return int(value or 0)
    except (TypeError, ValueError): return 0
def parse_token_event(obj, meta, context, titles, tz):
    raw, stamp = ((obj.get("payload") or {}).get("info") or {}).get("last_token_usage") or {}, obj.get("timestamp")
    if not raw or not stamp: return None
    try: timestamp = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(tz)
    except ValueError: return None
    input_, cached, output, reasoning = number(raw.get("input_tokens")), number(raw.get("cached_input_tokens")), number(raw.get("output_tokens")), number(raw.get("reasoning_output_tokens"))
    return UsageEvent(meta.session_id, timestamp, TokenUsage(input_, cached, output, reasoning, number(raw.get("total_tokens")) or input_ + output), context.model, context.effort, meta.originator, meta.source, meta.project, titles.get(meta.session_id))
def iter_session_events(path, tz, titles, stats):
    sid, meta, context = session_id(path), SessionMeta(session_id(path)), TurnContext()
    try:
        with open_text_with_retry(path) as h:
            stats.files_read += 1
            for line in h:
                if not any(x in line for x in ('"session_meta"','"turn_context"','"token_count"')): continue
                try: obj = json.loads(line)
                except json.JSONDecodeError: stats.malformed_json_lines += 1; continue
                payload, kind = obj.get("payload") or {}, obj.get("type")
                if kind == "session_meta": meta = parse_session_meta(payload, sid)
                elif kind == "turn_context": context = parse_turn_context(payload, context)
                elif kind == "event_msg" and payload.get("type") == "token_count":
                    event = parse_token_event(obj, meta, context, titles, tz)
                    if event: yield event
    except OSError: stats.files_skipped += 1
def event_identity(event):
    u = event.usage; return event.session_id, event.timestamp.isoformat(), u.total, u.input, u.cached_input, u.output, u.reasoning
def iter_token_events(home, tz, stats=None):
    stats, titles, seen = stats or DiscoveryStats(), load_session_titles(home, stats), set()
    for path in discover_session_files(home, stats=stats):
        for event in iter_session_events(path, tz, titles, stats):
            identity = event_identity(event)
            if identity not in seen: seen.add(identity); yield event

def day_count(start,end): return max((end-start).days+1,1)
def sum_usage(events):
    out=TokenUsage()
    for e in events: out.add(e.usage)
    return out
def usage_row(u): return {"total":u.total,"input":u.input,"cached_input":u.cached_input,"output":u.output,"reasoning":u.reasoning,"non_cached_input":u.fresh_input,"net_usage":u.net_usage,"cache_hit_rate":u.cache_hit_rate}
def summarize(events, days=None):
    u=sum_usage(events); out=usage_row(u); out.update(calls=len(events), sessions=len({e.session_id for e in events}), daily_average_total=u.total/days if days else None); return out
def week_start(day): return day-timedelta(days=day.weekday())
def grouped(events,key):
    out={}
    for e in events: out.setdefault(key(e),[]).append(e)
    return out
def weekly(events): return [{"start":k,"end":k+timedelta(days=6),"summary":summarize(v)} for k,v in sorted(grouped(events,lambda e:week_start(e.day)).items())]
def daily(events,start=None,end=None):
    buckets=grouped(events,lambda e:e.day)
    if start is not None and end is not None:
        for n in range((end-start).days+1): buckets.setdefault(start+timedelta(days=n),[])
    return [{"date":k,"summary":summarize(v)} for k,v in sorted(buckets.items())]
def summarize_dimension(events, attribute, extra=None):
    buckets=grouped(events,lambda e:(getattr(e,attribute) or "<unknown>",getattr(e,extra) or "<unknown>" if extra else None)); total=sum_usage(events).total; out=[]
    for key,bucket in buckets.items():
        u=sum_usage(bucket); row={attribute:key[0],"events":len(bucket),"sessions":len({e.session_id for e in bucket}),"usage":usage_row(u),"share_total":u.total/total if total else 0}
        if extra: row[extra]=key[1]
        out.append(row)
    return sorted(out,key=lambda x:x["usage"]["total"],reverse=True)
def summarize_models(events): return summarize_dimension(events,"model")
def summarize_efforts(events): return summarize_dimension(events,"effort","model")
def summarize_clients(events): return [{"originator":r["originator"],"source":r["source"],"events":r["events"],"sessions":r["sessions"],"usage":r["usage"],"share_total":r["share_total"]} for r in summarize_dimension(events,"originator","source")]
def summarize_projects(events): return summarize_dimension(events,"project")
def summarize_sessions(events):
    out=[]
    for sid,bucket in grouped(events,lambda e:e.session_id).items():
        u,models,efforts=sum_usage(bucket),{},{}
        for e in bucket: models.setdefault(e.model or "<unknown>",TokenUsage()).add(e.usage); efforts.setdefault(e.effort or "<unknown>",TokenUsage()).add(e.usage)
        model=max(models,key=lambda k:models[k].total) if models else None; effort=max(efforts,key=lambda k:efforts[k].total) if efforts else None; x=bucket[-1]
        out.append(SessionSummary(sid,x.title,x.project,x.originator,x.source,len(bucket),u,min(e.timestamp for e in bucket),max(e.timestamp for e in bucket),model,models[model].total/u.total if model and u.total else 0,effort,u.total/len(bucket),models,efforts))
    return sorted(out,key=lambda x:x.usage.total,reverse=True)
def detect_usage_diagnostics(summary,sessions):
    out=[]
    for x in sessions:
        share=x.usage.total/summary["total"] if summary["total"] else 0; duration=(x.last_event.date()-x.first_event.date()).days+1; label=x.title or "A local session"
        if share>=.3: out.append({"severity":"warning","code":"dominant_session","title":"Dominant session","message":f"{label} accounted for {share:.1%} of local token usage.","metric":share})
        if x.events>=20 and x.average_tokens_per_event>=150000: out.append({"severity":"info","code":"large_context","title":"Large context per event","message":f"{label} averaged {x.average_tokens_per_event:,.0f} tokens per event.","metric":x.average_tokens_per_event})
        if x.usage.cached_input>=100000000 and x.usage.cache_hit_rate>=.9: out.append({"severity":"info","code":"heavy_cached_context","title":"Heavy cached context","message":f"{label} repeatedly reused a very large cached context.","metric":x.usage.cached_input})
        if duration>=7 and x.usage.total>=100000000: out.append({"severity":"info","code":"long_lived_high_volume","title":"Long-lived high-volume session","message":f"{label} spans {duration} days with high local usage.","metric":duration})
    return out
def public_sessions(sessions,privacy):
    out=[]
    for i,x in enumerate(sessions,1): out.append({"session":f"Session #{i}" if privacy=="strict" else (x.title or f"Session #{i}"),"project":None if privacy=="strict" else x.project,"originator":None if privacy=="strict" else x.originator,"source":None if privacy=="strict" else x.source,"events":x.events,"usage":usage_row(x.usage),"dominant_model":x.dominant_model,"dominant_model_share":x.dominant_model_share,"dominant_effort":x.dominant_effort,"average_tokens_per_event":x.average_tokens_per_event,"first_event":x.first_event,"last_event":x.last_event})
    return out
def build_report(start,end,events,*,machine_name=None,parser_stats=None,privacy="standard"):
    events=list(events); stats=parser_stats or DiscoveryStats(); days=day_count(start,end); summary=summarize(events,days); sessions=summarize_sessions(events); weeks,days_rows=weekly(events),daily(events,start,end)
    return {"schema_version":2,"start":start,"end":end,"days":days,"machine":{"name":machine_name or socket.gethostname()},"summary":summary,"peak_week":max(weeks,key=lambda r:r["summary"]["total"],default=None),"peak_day":max((r for r in days_rows if r["summary"]["calls"]),key=lambda r:r["summary"]["total"],default=None),"weeks":weeks,"daily":days_rows,"models":summarize_models(events),"efforts":summarize_efforts(events),"sessions_detail":public_sessions(sessions,privacy),"clients":summarize_clients(events),"projects":summarize_projects(events),"diagnostics":detect_usage_diagnostics(summary,sessions),"parser":asdict(stats),"privacy":privacy}
def fmt(v): return f"{v:,.2f}" if isinstance(v,float) and not v.is_integer() else f"{int(v) if isinstance(v,float) else v:,}"
def json_ready(v):
    if is_dataclass(v): return json_ready(asdict(v))
    if isinstance(v,(date,datetime)): return v.isoformat()
    if isinstance(v,dict): return {k:json_ready(x) for k,x in v.items()}
    if isinstance(v,list): return [json_ready(x) for x in v]
    return v
def print_json(report): print(json.dumps(json_ready(report),ensure_ascii=False,indent=2))
def print_markdown(report,language,top_sessions=10):
    s=report["summary"]; print(f"Range: {report['start']} to {report['end']}\nCalls: {s['calls']:,}\nSessions: {s['sessions']:,}\n")
    print("| Metric | Tokens | Notes |\n|---|---:|---|")
    for key,label,note in [("total","Total","Sum of `total_tokens`"),("input","Input","Input tokens, including cached input"),("cached_input","Cached input","Cached input tokens"),("output","Output","Output tokens"),("reasoning","Reasoning output","Reasoning output tokens"),("non_cached_input","Non-cached input","`Input - Cached input`"),("net_usage","Net usage","`Non-cached input + Output`")]: print(f"| {label} | {fmt(s[key])} | {note} |")
    print(f"| Cache hit rate | {s['cache_hit_rate']*100:.2f}% | `Cached input / Input` |\n| Daily average total | {fmt(s['daily_average_total'])} | `Total / days in range` |")
    if report["peak_day"]: print(f"\nPeak day: {report['peak_day']['date']}, {fmt(report['peak_day']['summary']['total'])} tokens.")
    if report["peak_week"]: print(f"Busiest week: {report['peak_week']['start']} to {report['peak_week']['end']}, {fmt(report['peak_week']['summary']['total'])} tokens.")
    print("\n### Daily usage\n\n| Date | Total | Input | Cached input | Output | Net usage |\n|---|---:|---:|---:|---:|---:|")
    for r in report["daily"]: u=r["summary"]; print(f"| {r['date']} | {fmt(u['total'])} | {fmt(u['input'])} | {fmt(u['cached_input'])} | {fmt(u['output'])} | {fmt(u['net_usage'])} |")
    print("\n## By model\n\n| Model | Total | Events |\n|---|---:|---:|"+"\n".join(f"| {r['model']} | {fmt(r['usage']['total'])} | {r['events']:,} |" for r in report["models"][:10]))
    print("\n## Top sessions\n\n| Session | Total | Events |\n|---|---:|---:|"+"\n".join(f"| {r['session']} | {fmt(r['usage']['total'])} | {r['events']:,} |" for r in report["sessions_detail"][:top_sessions]))
    if report["diagnostics"]: print("\n## Diagnostics\n\n"+"\n".join(f"- {r['title']}: {r['message']}" for r in report["diagnostics"]))
def render_html(report,language="zh"):
    template=pathlib.Path(__file__).resolve().parent.parent/"assets"/"dashboard.html"; data=json.dumps(json_ready(report),ensure_ascii=False).replace("&","\\u0026").replace("<","\\u003c").replace(">","\\u003e"); return template.read_text(encoding="utf-8").replace("__REPORT_JSON__",data).replace("__LANGUAGE__",language)
def open_external_report(path):
    path=path.resolve()
    try:
        if sys.platform=="win32": os.startfile(str(path))
        elif not webbrowser.open(path.as_uri(),new=2): raise OSError("No external browser accepted the open request")
    except (OSError,webbrowser.Error) as error: print(f"Report saved; could not request external browser: {error}. Open {path} manually.",file=sys.stderr); return False
    print(f"External browser open requested: {path}"); return True
def main():
    args=parse_args(); tz=ZoneInfo(args.timezone) if args.timezone else datetime.now().astimezone().tzinfo; home=pathlib.Path(args.codex_home).expanduser(); start,end=resolve_range(args,tz)
    if start>end or (args.days is not None and args.days<1): raise SystemExit("Invalid range: start must be on or before end, and days must be positive.")
    stats=DiscoveryStats(); events=[e for e in iter_token_events(home,tz,stats) if start<=e.day<=end]; report=build_report(start,end,events,machine_name=args.machine_name,parser_stats=stats,privacy=args.privacy); report["timezone"]=args.timezone or str(tz); report["generated_at"]=datetime.now(tz).isoformat(timespec="seconds")
    if args.format=="html": output=render_html(report,args.language); args.output=args.output or pathlib.Path.cwd()/"output"/f"token-usage-{start}-{end}.html"
    else:
        b=io.StringIO()
        with contextlib.redirect_stdout(b): print_json(report) if args.format=="json" else print_markdown(report,args.language,args.top_sessions)
        output=b.getvalue()
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(output,encoding="utf-8"); print(f"Report written to {args.output.resolve()}")
        if args.format=="html" and not args.no_open: open_external_report(args.output)
    else: print(output,end="")
if __name__=="__main__": main()
