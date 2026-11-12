"""Czat z modelem przez OmniRoute (lub inne API zgodne z OpenAI) z narzędziami tylko do odczytu repozytorium.
Jedna rozmowa na projekt, historia w DevKit/data/claude-chat.json."""
import fnmatch, json, os, re, threading, time, urllib.error, urllib.request
from pathlib import Path

ROOT = DATA = None
GET_CFG = None
LOCK = threading.RLock()
STATE = {"busy": False, "rev": 0, "live": "", "status": "", "stop": False}
MAX_CONTEXT_CHARS = 120_000
MAX_TOOL_CHARS = 40_000
MAX_STEPS = 14

# nie czytamy sekretów ani śmieci (modele są zdalne, więc klucze i hasła nie mogą do nich trafić)
DENY_DIRS = {"node_modules", ".git", ".next", "__pycache__", "dist", "build", "keys", ".venv", "venv", ".idea", ".vscode"}
DENY_SUFFIX = {".pem", ".key", ".p12", ".pfx", ".sqlite", ".db"}
SKIP_TEXT_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".woff", ".woff2", ".ttf", ".zip", ".exe", ".pdf", ".mp4", ".mp3"}

TOOLS = [
    {"type": "function", "function": {
        "name": "list_dir", "description": "Wypisuje zawartość folderu w repozytorium (ścieżka względna od roota repo).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "np. web/app; domyślnie root repo"}}}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "Czyta plik tekstowy z numerami linii. Duże pliki czytaj we fragmentach (start_line, end_line).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "start_line": {"type": "integer", "description": "domyślnie 1"},
            "end_line": {"type": "integer", "description": "domyślnie start_line + 399"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "search", "description": "Szuka wyrażenia regularnego (bez rozróżniania wielkości liter) w plikach tekstowych repozytorium.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"}, "path": {"type": "string", "description": "folder do przeszukania, domyślnie root repo"},
            "glob": {"type": "string", "description": "opcjonalnie np. *.tsx"}}, "required": ["pattern"]}}},
]


def configure(root, data_dir, get_cfg):
    global ROOT, DATA, GET_CFG
    ROOT, DATA, GET_CFG = Path(root).resolve(), Path(data_dir), get_cfg


# ---------- historia ----------
def _file(): return DATA / "claude-chat.json"


def _load():
    try: return json.loads(_file().read_text("utf-8")).get("messages", [])
    except Exception: return []


def _save(messages):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = _file().with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "messages": messages}, ensure_ascii=False), "utf-8")
    tmp.replace(_file())


def _append(msg):
    with LOCK:
        msg["ts"] = int(time.time())
        messages = _load(); messages.append(msg); _save(messages)
        STATE["rev"] += 1


def history():
    with LOCK: return _load()


def clear():
    with LOCK:
        if STATE["busy"]: raise RuntimeError("Poczekaj na koniec odpowiedzi albo ją zatrzymaj.")
        _file().unlink(missing_ok=True); STATE["rev"] += 1


def state():
    with LOCK:
        c = GET_CFG()["claude"]
        return {"busy": STATE["busy"], "rev": STATE["rev"], "live": STATE["live"], "status": STATE["status"],
                "configured": bool(c["base_url"].strip() and c["model"].strip()), "model": c["model"]}


def stop():
    STATE["stop"] = True


# ---------- narzędzia (tylko odczyt) ----------
class ToolError(Exception): pass


def _denied(p):
    rel = p.relative_to(ROOT)
    if any(part in DENY_DIRS for part in rel.parts): return True
    if p.name.lower().startswith(".env") or p.suffix.lower() in DENY_SUFFIX: return True
    try: p.relative_to(DATA.resolve()); return True      # dane DevKita: hasła, zrzuty baz, historia
    except ValueError: return False


def _safe(rel):
    p = (ROOT / (rel or ".")).resolve()
    try: p.relative_to(ROOT)
    except ValueError: raise ToolError("Ścieżka poza repozytorium.")
    if p != ROOT and _denied(p): raise ToolError("Odmowa: ten plik lub folder jest na liście wyłączeń (sekrety, zależności, dane DevKita).")
    return p


def _is_binary(p):
    try: return b"\0" in p.open("rb").read(4096)
    except OSError: return True


def tool_list_dir(path="."):
    p = _safe(path)
    if not p.is_dir(): raise ToolError("To nie jest folder.")
    out = []
    for e in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
        if _denied(e): continue
        out.append(e.name + "/" if e.is_dir() else f"{e.name}  ({e.stat().st_size} B)")
        if len(out) >= 300: out.append("... (lista skrócona)"); break
    return "\n".join(out) or "(pusto)"


def tool_read_file(path, start_line=1, end_line=None):
    p = _safe(path)
    if not p.is_file(): raise ToolError("To nie jest plik.")
    if p.suffix.lower() in SKIP_TEXT_SUFFIX or _is_binary(p): raise ToolError("Plik binarny, nie da się go odczytać jako tekst.")
    if p.stat().st_size > 5_000_000: raise ToolError("Plik jest za duży (ponad 5 MB).")
    lines = p.read_text("utf-8", errors="replace").splitlines()
    a = max(1, int(start_line or 1)); b = min(len(lines), int(end_line or a + 399))
    out, size = [], 0
    for n in range(a, b + 1):
        row = f"{n}| {lines[n - 1]}"
        size += len(row) + 1
        if size > MAX_TOOL_CHARS: out.append(f"... (przerwano na linii {n - 1}, czytaj dalej od linii {n})"); break
        out.append(row)
    out.append(f"[plik ma {len(lines)} linii, pokazano {a}-{min(b, a + len(out) - 1)}]")
    return "\n".join(out)


def tool_search(pattern, path=".", glob=None):
    base = _safe(path)
    try: rx = re.compile(pattern, re.I)
    except re.error as e: raise ToolError(f"Niepoprawne wyrażenie regularne: {e}")
    hits, t0, files = [], time.time(), 0
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if not _denied(Path(dirpath) / d)]
        for fn in filenames:
            f = Path(dirpath) / fn
            if _denied(f) or f.suffix.lower() in SKIP_TEXT_SUFFIX or (glob and not fnmatch.fnmatch(fn, glob)): continue
            files += 1
            if files > 20000 or time.time() - t0 > 8: return "\n".join(hits) + "\n... (przerwano: za dużo plików albo za długo)"
            try:
                if f.stat().st_size > 1_000_000 or _is_binary(f): continue
                for n, line in enumerate(f.read_text("utf-8", errors="replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{f.relative_to(ROOT).as_posix()}:{n}: {line.strip()[:200]}")
                        if len(hits) >= 80: return "\n".join(hits) + "\n... (jest więcej wyników, zawęź wyszukiwanie)"
            except OSError: continue
    return "\n".join(hits) or "Brak wyników."


def run_tool(name, args):
    try:
        data = json.loads(args) if isinstance(args, str) and args.strip() else (args or {})
        fn = {"list_dir": tool_list_dir, "read_file": tool_read_file, "search": tool_search}.get(name)
        if not fn: return f"Nieznane narzędzie: {name}"
        return fn(**data)[:MAX_TOOL_CHARS + 500]
    except ToolError as e: return f"Błąd: {e}"
    except TypeError as e: return f"Błąd argumentów: {e}"
    except json.JSONDecodeError: return "Błąd: argumenty nie są poprawnym JSON-em."
    except Exception as e: return f"Błąd: {type(e).__name__}: {e}"


def _tree(limit=160, depth=3):
    lines = []
    def walk(d, level):
        if level > depth or len(lines) >= limit: return
        try: entries = sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except OSError: return
        for e in entries:
            if len(lines) >= limit: lines.append("  " * level + "..."); return
            if _denied(e): continue
            lines.append("  " * level + e.name + ("/" if e.is_dir() else ""))
            if e.is_dir(): walk(e, level + 1)
    walk(ROOT, 0)
    return "\n".join(lines)


def system_prompt(with_tools=True):
    tools = ("Masz narzędzia TYLKO do odczytu: list_dir, read_file, search. Zanim odpowiesz o kodzie, przeczytaj odpowiednie pliki i nie zgaduj ich zawartości. "
             "Nie możesz zapisywać plików ani uruchamiać poleceń, więc gdy trzeba coś zmienić, podaj gotowy kod z nazwą pliku. "
             "Sekrety (.env, klucze .pem, dane DevKita) są niedostępne. ") if with_tools else \
            "W tej rozmowie nie masz dostępu do plików (model nie obsługuje narzędzi). Proś użytkownika o wklejenie kodu. "
    return ("Jesteś asystentem programisty wbudowanym w DevKit. Odpowiadasz po polsku, zwięźle i konkretnie. "
            f"Pracujesz w repozytorium: {ROOT}. {tools}\n\nStruktura repozytorium (skrócona):\n{_tree()}")


# ---------- API (OpenAI-compatible) ----------
def _open(cfg, path, payload=None, timeout=300):
    url = cfg["base_url"].rstrip("/") + path
    req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                                 method="POST" if payload is not None else "GET")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream" if (payload or {}).get("stream") else "application/json")
    if cfg["api_key"]: req.add_header("Authorization", "Bearer " + cfg["api_key"])
    local = re.match(r"https?://(localhost|127\.0\.0\.1|\[::1\])", url, re.I)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()
    return opener.open(req, timeout=timeout)


def _http_error(e):
    try:
        body = e.read().decode("utf-8", "replace")
        msg = json.loads(body).get("error", body)
        msg = msg.get("message", msg) if isinstance(msg, dict) else msg
    except Exception:
        msg = e.reason
    return f"Błąd API ({e.code}): {str(msg)[:600]}"


def list_models():
    cfg = GET_CFG()["claude"]
    try:
        with _open(cfg, "/models", timeout=15) as r: data = json.load(r)
    except urllib.error.HTTPError as e: raise RuntimeError(_http_error(e))
    except Exception as e: raise RuntimeError(f"Nie można połączyć się z {cfg['base_url']}: {e}")
    ids = sorted({m.get("id") for m in data.get("data", []) if m.get("id")}, key=lambda x: ("claude" not in x.lower(), x.lower()))
    return ids


def _text_of(content):
    if isinstance(content, list): return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


def _stream(cfg, payload):
    """Zwraca (tekst, tool_calls). Aktualizuje STATE['live'] w trakcie strumienia."""
    text, calls = [], {}
    with _open(cfg, "/chat/completions", payload) as resp:
        if "text/event-stream" in resp.headers.get("Content-Type", ""):
            for raw in resp:
                if STATE["stop"]: break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"): continue
                chunk = line[5:].strip()
                if chunk == "[DONE]": break
                try: obj = json.loads(chunk)
                except ValueError: continue
                if obj.get("error"): raise RuntimeError(str(obj["error"])[:600])
                delta = ((obj.get("choices") or [{}])[0]).get("delta") or {}
                piece = _text_of(delta.get("content"))
                if piece:
                    text.append(piece)
                    with LOCK: STATE["live"] = "".join(text)
                for tc in delta.get("tool_calls") or []:
                    c = calls.setdefault(tc.get("index", len(calls)), {"id": "", "name": "", "args": ""})
                    c["id"] = tc.get("id") or c["id"]
                    f = tc.get("function") or {}
                    c["name"] += f.get("name") or ""
                    c["args"] += f.get("arguments") or ""
        else:                                    # dostawca nie strumieniuje: zwykła odpowiedź JSON
            msg = (json.load(resp).get("choices") or [{}])[0].get("message") or {}
            text.append(_text_of(msg.get("content")))
            with LOCK: STATE["live"] = "".join(text)
            for i, tc in enumerate(msg.get("tool_calls") or []):
                f = tc.get("function") or {}
                calls[i] = {"id": tc.get("id", ""), "name": f.get("name", ""), "args": f.get("arguments", "")}
    tool_calls = [{"id": c["id"] or f"call_{int(time.time() * 1000)}_{i}", "type": "function",
                   "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                  for i, c in sorted(calls.items()) if c["name"]]
    return "".join(text), tool_calls


def _api_messages(messages):
    """Historia do wysłania: bez błędów, z kompletnymi parami tool_calls -> tool, przycięta do budżetu znaków."""
    clean, i = [], 0
    while i < len(messages):
        m = messages[i]
        if m["role"] == "assistant" and m.get("tool_calls"):
            ids = {c["id"] for c in m["tool_calls"]}
            j, results = i + 1, []
            while j < len(messages) and messages[j]["role"] == "tool":
                results.append(messages[j]); j += 1
            if ids <= {r.get("tool_call_id") for r in results}: clean.append(m); clean.extend(results)
            i = j
            continue
        if m["role"] in ("user", "assistant", "tool"): clean.append(m)
        i += 1
    total, start = 0, len(clean)
    for k in range(len(clean) - 1, -1, -1):
        total += len(json.dumps(clean[k], ensure_ascii=False))
        if total > MAX_CONTEXT_CHARS: break
        start = k
    while start < len(clean) - 1 and clean[start]["role"] != "user": start += 1   # nie zaczynamy w środku wymiany
    out = []
    for m in clean[start:]:
        d = {"role": m["role"], "content": m.get("content")}
        if m.get("tool_calls"): d["tool_calls"] = m["tool_calls"]
        if m.get("tool_call_id"): d["tool_call_id"] = m["tool_call_id"]
        out.append(d)
    return out


def _run(text):
    cfg = GET_CFG()["claude"]
    tools_on = True
    try:
        _append({"role": "user", "content": text})
        for _ in range(MAX_STEPS):
            if STATE["stop"]: break
            with LOCK: STATE["live"], STATE["status"] = "", "Model myśli..."
            payload = {"model": cfg["model"], "stream": True,
                       "messages": [{"role": "system", "content": system_prompt(tools_on)}] + _api_messages(history())}
            if tools_on: payload["tools"] = TOOLS
            try:
                reply, calls = _stream(cfg, payload)
            except urllib.error.HTTPError as e:
                msg = _http_error(e)
                if tools_on and e.code in (400, 422) and re.search(r"tool|function", msg, re.I):
                    tools_on = False
                    _append({"role": "error", "content": "Model nie obsługuje narzędzi, odpowiadam bez dostępu do plików."})
                    continue
                raise RuntimeError(msg)
            if reply or calls:
                m = {"role": "assistant", "content": reply or None}
                if calls: m["tool_calls"] = calls
                _append(m)
            if not calls: break
            for c in calls:
                name, args = c["function"]["name"], c["function"]["arguments"]
                if STATE["stop"]:
                    result = "Przerwano przez użytkownika."
                else:
                    with LOCK: STATE["status"], STATE["live"] = f"{name} {args[:120]}", ""
                    result = run_tool(name, args)
                _append({"role": "tool", "tool_call_id": c["id"], "name": name, "content": result})
        else:
            _append({"role": "error", "content": f"Przerwano po {MAX_STEPS} krokach narzędzi. Napisz „kontynuuj”, żeby iść dalej."})
    except urllib.error.URLError as e:
        _append({"role": "error", "content": f"Nie można połączyć się z {cfg['base_url']} ({e.reason}). Czy OmniRoute jest uruchomiony?"})
    except Exception as e:
        _append({"role": "error", "content": str(e) or type(e).__name__})
    finally:
        with LOCK: STATE.update(busy=False, live="", status="", stop=False)
        with LOCK: STATE["rev"] += 1


def send(text):
    text = (text or "").strip()
    if not text: raise ValueError("Wpisz wiadomość.")
    cfg = GET_CFG()["claude"]
    if not cfg["base_url"].strip() or not cfg["model"].strip(): raise RuntimeError("Uzupełnij adres OmniRoute i model w Ustawieniach.")
    with LOCK:
        if STATE["busy"]: raise RuntimeError("Model jeszcze odpowiada.")
        STATE.update(busy=True, live="", status="Łączenie...", stop=False)
    threading.Thread(target=_run, args=(text,), daemon=True).start()
