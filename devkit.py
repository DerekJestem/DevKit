"""DevKit - lokalny zestaw narzędzi do pracy nad projektem (backend).
Tylko biblioteka standardowa. Uruchomienie: py devkit.py  (lub skompilowany DevKit.exe w roocie repo)."""
import json, os, re, shutil, signal, socket, subprocess, sys, tempfile, threading, time, secrets, webbrowser
import urllib.request, urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import claudechat, dumpparse

FROZEN = getattr(sys, "frozen", False)
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))      # tu leży ui/
ROOT = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent.parent  # root repo
DEVKIT_DIR = (ROOT / "DevKit") if FROZEN else Path(__file__).resolve().parent
DATA = DEVKIT_DIR / "data"
DUMPS = DATA / "dumps"
CFG_FILE = DATA / "config.json"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEWGRP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)
TOKEN = secrets.token_urlsafe(24)
PORT = 0
LAST_PING = [time.time()]
NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$")   # litery, cyfry, _ i - (nie na początku)
SYSTEM_DBS = {"information_schema", "mysql", "performance_schema", "sys", "phpmyadmin", "test"}

NEXT_CMD = ('npx create-next-app@latest web --ts --eslint --no-tailwind --no-react-compiler --no-src-dir '
            '--cache-components --agents-md --app --import-alias "@/*" --use-npm --yes')
DEFAULTS = {
    "db_name": "", "xampp": r"C:\xampp", "db_host": "127.0.0.1", "db_port": 3306, "apache_port": 80,
    "db_user": "root", "db_pass": "", "drop_after_export": True,
    "xampp_installer": "",     # ścieżka do instalatora XAMPP (.exe), otwierana przyciskiem Zainstaluj
    "claude": {"base_url": "http://localhost:20128/v1", "api_key": "", "model": ""},   # OmniRoute (API zgodne z OpenAI)
    "layouts": {},             # położenia tabel w podglądzie bazy: {nazwa_bazy: {tabela: [x, y]}}
    "browser": "edge", "private": True,
    "links": [
        {"name": "Notion", "url": "https://www.notion.so"},
        {"name": "GitHub", "url": "https://github.com"},
        {"name": "Figma", "url": "https://www.figma.com"},
        {"name": "mikr.us", "url": "https://mikr.us"},
    ],
    "projects": {
        "web": {"folder": "web", "create": NEXT_CMD, "post": "", "run": "npm run dev",
                "install": "npm install", "port": 3000,
                "env": "FORCE_COLOR=1", "inside": False, "interactive": False},
        "api": {"folder": "api", "create": "npx express-generator", "post": "npm install && npm install cors",
                "run": "npm start", "install": "npm install", "port": 4000,
                "env": "PORT=4000\nFORCE_COLOR=1", "inside": True, "interactive": False},
    },
}
BROWSERS = {
    "chrome": ("Chrome", r"Google\Chrome\Application\chrome.exe", "--incognito"),
    "brave": ("Brave", r"BraveSoftware\Brave-Browser\Application\brave.exe", "--incognito"),
    "firefox": ("Firefox", r"Mozilla Firefox\firefox.exe", "-private-window"),
    "edge": ("Edge", r"Microsoft\Edge\Application\msedge.exe", "--inprivate"),
}

# ---------- konfiguracja ----------
def load_cfg():
    c = json.loads(json.dumps(DEFAULTS))
    try: saved = json.loads(CFG_FILE.read_text("utf-8"))
    except Exception: saved = {}
    projects = saved.pop("projects", {})
    claude = saved.pop("claude", {})
    c.update(saved)
    c["claude"] = {**DEFAULTS["claude"], **(claude if isinstance(claude, dict) else {})}
    for k, dflt in DEFAULTS["projects"].items():
        c["projects"][k] = {**dflt, **(projects.get(k) or {})}
    return c

def save_cfg(c):
    DATA.mkdir(parents=True, exist_ok=True)
    CFG_FILE.write_text(json.dumps(c, indent=2, ensure_ascii=False), "utf-8")

# ---------- przeglądarka ----------
def find_browser(key):
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        base = os.environ.get(env)
        if base and (Path(base) / BROWSERS[key][1]).exists():
            return str(Path(base) / BROWSERS[key][1])
    return shutil.which(Path(BROWSERS[key][1]).name)

FF_USER_JS = '''user_pref("browser.cache.disk.enable", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("privacy.sanitize.sanitizeOnShutdown", false);
user_pref("network.cookie.lifetimePolicy", 0);
'''
FF_IGNORE = '''# Firefox z profilem w DevKit: cache i pliki tymczasowe nie trafiają do repo
firefox-profile/lock
firefox-profile/.parentlock
firefox-profile/parent.lock
firefox-profile/cache2/
firefox-profile/startupCache/
firefox-profile/shader-cache/
firefox-profile/crashes/
firefox-profile/minidumps/
firefox-profile/datareporting/
firefox-profile/saved-telemetry-pings/
firefox-profile/safebrowsing/
firefox-profile/thumbnails/
firefox-profile/storage/temporary/
firefox-profile/gmp-*/
firefox-profile/*.log
'''

def open_urls(urls):
    urls = [u for u in urls if isinstance(u, str) and re.match(r"^https?://", u)]
    if not urls: return {"ok": False, "error": "Adres musi zaczynać się od http:// lub https://"}
    c = load_cfg(); b = c["browser"]
    if b == "firefox-portable":      # sesje logowania trzymane w folderze DevKit (przenoszą się z repo)
        p = find_browser("firefox")
        if p:
            prof = DATA / "firefox-profile"; prof.mkdir(parents=True, exist_ok=True)
            if not (prof / "user.js").exists(): (prof / "user.js").write_text(FF_USER_JS, "utf-8")
            if not (DATA / ".gitignore").exists(): (DATA / ".gitignore").write_text(FF_IGNORE, "utf-8")
            subprocess.Popen([p, "-profile", str(prof), *urls])
            return {"ok": True, "msg": "Otwarto w Firefoksie z profilem DevKit (sesje zapisują się w repo)"}
        webbrowser.open(urls[0])
        return {"ok": True, "warn": True, "msg": "Nie znaleziono Firefoksa. Otwarto w domyślnej przeglądarce."}
    if b in BROWSERS:
        p = find_browser(b)
        if p:
            cmd = [p] + ([BROWSERS[b][2]] if c["private"] else []) + urls
            subprocess.Popen(cmd)
            return {"ok": True, "msg": f"Otwarto w {BROWSERS[b][0]}" + (" (tryb prywatny)" if c["private"] else "")}
        webbrowser.open(urls[0])
        return {"ok": True, "warn": True, "msg": f"Nie znaleziono {BROWSERS[b][0]}. Otwarto w domyślnej przeglądarce, bez trybu prywatnego."}
    for u in urls: webbrowser.open(u)
    return {"ok": True, "warn": True, "msg": "Otwarto w domyślnej przeglądarce. Tryb prywatny nie jest tu dostępny."}

def open_app(url):
    """Otwiera DevKit jako osobne okno bez pasków przeglądarki (tryb --app, własny profil w TEMP)."""
    c = load_cfg()
    p = next((find_browser(k) for k in [c["browser"], "edge", "chrome", "brave"] if k in ("edge", "chrome", "brave") and find_browser(k)), None)
    if not p: return open_urls([url])
    start = time.time()
    proc = subprocess.Popen([p, f"--app={url}", "--window-size=1180,780", f"--user-data-dir={Path(tempfile.gettempdir()) / 'devkit-profile'}",
                             "--no-first-run", "--no-default-browser-check"])
    def waiter():   # zamknięcie okna zamyka DevKit
        proc.wait()
        if time.time() - start > 3: die()
    threading.Thread(target=waiter, daemon=True).start()

# ---------- XAMPP: MySQL i Apache ----------
def xp(c): return Path(c["xampp"])
def bins(c):
    b = xp(c) / "mysql" / "bin"
    return b / "mysql.exe", b / "mysqldump.exe"

def conn(c):
    a = [f"--host={c['db_host']}", f"--port={c['db_port']}", f"--user={c['db_user']}"]
    if c["db_pass"]: a.append(f"--password={c['db_pass']}")
    return a

def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=NOWIN, timeout=kw.pop("timeout", 300), **kw)

def sql(c, query):
    return run([str(bins(c)[0]), *conn(c), "-N", "-B", "-e", query])

def port_open(host, port):
    try:
        with socket.create_connection((host, int(port)), timeout=0.5): return True
    except OSError: return False

def mysql_up(c): return port_open(c["db_host"], c["db_port"])
def apache_up(c): return port_open("127.0.0.1", c["apache_port"])

def wait_port(host, port, want, secs=10):
    for _ in range(secs * 2):
        time.sleep(0.5)
        if port_open(host, port) == want: return True
    return False

def check_name(n):
    if not NAME_RE.match(n or ""): raise ValueError("Nazwa bazy: litery, cyfry, _ i - (nie na początku), max 64 znaki.")
    return n

def xampp_installed(c):
    return (xp(c) / "xampp-control.exe").exists() or (xp(c) / "mysql" / "bin" / "mysqld.exe").exists()

def need_mysql(c):
    if not bins(c)[0].exists(): raise RuntimeError(f"Nie znaleziono mysql.exe w {bins(c)[0].parent}. Sprawdź ścieżkę XAMPP w Ustawieniach.")
    if not mysql_up(c): raise RuntimeError("MySQL nie działa. Uruchom go w sekcji Serwer.")

def spawn(cmd, cwd):
    subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=NOWIN | DETACHED)

# ---------- procesy projektów (web / api) ----------
class Job:
    def __init__(s):
        s.lines, s.dropped, s.p, s.mode, s.busy, s.lock = [], 0, None, "", False, threading.Lock()
        s.gen, s.phase, s.ext_noted = 0, "", False
    def add(s, text):
        with s.lock:
            s.lines.append(text)
            if len(s.lines) > 3000: del s.lines[:500]; s.dropped += 500
    def since(s, n):
        with s.lock: return s.lines[max(0, n - s.dropped):], s.dropped + len(s.lines)
    def alive(s): return s.busy or (s.p is not None and s.p.poll() is None)

JOBS = {k: Job() for k in DEFAULTS["projects"]}

def kill(j):
    p = j.p
    if p and p.poll() is None:
        if os.name == "nt": subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, creationflags=NOWIN)
        else:
            try: os.killpg(p.pid, signal.SIGKILL)
            except Exception: p.kill()

def die():
    for j in JOBS.values(): kill(j)
    os._exit(0)

def parse_env(text):
    env = dict(os.environ)
    for line in (text or "").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1); env[k.strip()] = v.strip()
    return env

def run_stream(j, cmd, cwd, env):
    j.add(f"$ {cmd}")
    p = subprocess.Popen(cmd, shell=True, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                         creationflags=NOWIN | NEWGRP, start_new_session=(os.name != "nt"))
    j.p = p; out = []
    for line in p.stdout:
        line = line.rstrip("\r\n"); out.append(line); j.add(line)
    p.wait(); j.add(f"[zakończono, kod {p.returncode}]")
    return p.returncode, "\n".join(out)

def proj_port(p):
    for line in (p.get("env") or "").splitlines():
        m = re.match(r"\s*PORT\s*=\s*(\d+)", line)
        if m: return int(m.group(1))
    return int(p["port"])

def pids_on_port(port):
    """PID-y procesów nasłuchujących na porcie (bez parsowania stanu, bo nazwy zależą od języka Windowsa)."""
    pids = set()
    if os.name == "nt":
        for line in run(["netstat", "-ano", "-p", "tcp"]).stdout.splitlines():
            x = line.split()
            if len(x) >= 5 and x[0] == "TCP" and x[2] in ("0.0.0.0:0", "[::]:0") and x[1].endswith(":" + str(port)):
                pids.add(int(x[-1]))
    elif shutil.which("ss"):
        for line in run(["ss", "-ltnpH"]).stdout.splitlines():
            if re.search(r":%d\s" % int(port), line):
                pids.update(int(m) for m in re.findall(r"pid=(\d+)", line))
    return sorted(pids)

def pname(pid):
    try:
        if os.name == "nt":
            return run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"]).stdout.split(",")[0].strip('"') or "proces"
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except Exception:
        return "proces"

def kill_pid(pid):
    if os.name == "nt": subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NOWIN)
    else:
        try: os.kill(pid, signal.SIGKILL)
        except Exception: pass

def free_port(j, port):
    """Zabija wszystko, co trzyma port projektu (np. proces z poprzedniej sesji DevKita)."""
    pids = [x for x in pids_on_port(port) if x != os.getpid()]
    for pid in pids:
        j.add(f"[DevKit] Kończę {pname(pid)} (PID {pid}) na porcie {port}")
        kill_pid(pid)
    for _ in range(20 if pids else 0):
        if not port_open("localhost", port): break
        time.sleep(0.25)
    return len(pids)

def job_state(k, c):
    """Zwraca (stan, czy_proces_spoza_DevKit). Stan 'running' obejmuje też procesy zostawione z poprzedniej sesji."""
    j, p = JOBS[k], c["projects"][k]
    if j.mode == "create" and j.busy: return "creating", False
    if j.mode == "run" and j.busy: return ("installing" if j.phase == "install" else "running"), False
    if (ROOT / p["folder"] / "package.json").is_file():
        return ("running", True) if port_open("localhost", proj_port(p)) else ("off", False)
    return "none", False

def create_job(k, c):
    p, j = c["projects"][k], JOBS[k]
    j.mode, j.busy = "create", True
    try:
        proot = ROOT / p["folder"]; env = parse_env(p["env"]); cwd = ROOT
        if p["inside"]: proot.mkdir(exist_ok=True); cwd = proot
        cmd, rc = p["create"], 1
        for _ in range(6):     # nieznane flagi (inna wersja narzędzia) pomijamy i próbujemy jeszcze raz
            rc, out = run_stream(j, cmd, cwd, env)
            m = re.search(r"unknown option '(--[\w-]+)'", out)
            if rc and m:
                new = re.sub(r"(?<!\S)" + re.escape(m.group(1)) + r"(?!\S)\s?", "", cmd, count=1)
                if new == cmd: break
                j.add(f"[DevKit] To narzędzie nie zna flagi {m.group(1)}. Pomijam ją i próbuję ponownie."); cmd = new; continue
            break
        if rc == 0 and p["post"].strip(): rc, _ = run_stream(j, p["post"], proot, env)
        j.add("[DevKit] Projekt utworzony." if rc == 0 else "[DevKit] Tworzenie nie powiodło się.")
    except Exception as e:
        j.add(f"[DevKit] Błąd: {e}")
    finally:
        j.busy = False

def run_job(k, c, gen):
    j, p = JOBS[k], c["projects"][k]
    folder, env = ROOT / p["folder"], parse_env(p["env"])
    try:
        free_port(j, proj_port(p))
        (folder / ".next" / "dev" / "lock").unlink(missing_ok=True)   # po zabitym procesie lock mógł zostać
        if j.gen != gen: return
        if not (folder / "node_modules").is_dir():
            j.phase = "install"
            j.add("[DevKit] Brak node_modules. Instaluję zależności przed startem.")
            rc, _ = run_stream(j, p["install"], folder, env)
            if j.gen != gen: return
            if rc:
                j.add("[DevKit] Instalacja zależności nie powiodła się. Nie uruchamiam projektu."); return
        j.phase = "run"
        run_stream(j, p["run"], folder, env)
    except Exception as e:
        j.add(f"[DevKit] Błąd: {e}")
    finally:
        if j.gen == gen: j.busy, j.phase = False, ""

def start_run(k, c):
    p, j = c["projects"][k], JOBS[k]
    if not (ROOT / p["folder"] / "package.json").is_file(): raise RuntimeError("Najpierw utwórz projekt.")
    if j.busy: raise RuntimeError("Projekt już działa lub jest w trakcie tworzenia.")
    j.mode, j.busy, j.phase = "run", True, ""
    threading.Thread(target=run_job, args=(k, c, j.gen), daemon=True).start()

def stop_run(k):
    j, c = JOBS[k], load_cfg()
    j.gen += 1                       # unieważnia wątek, który mógłby jeszcze odpalić kolejny krok
    kill(j)
    if j.p:
        try: j.p.wait(timeout=5)
        except Exception: pass
    j.busy, j.phase = False, ""
    free_port(j, proj_port(c["projects"][k]))   # także procesy spoza DevKita

# ---------- trasy ----------
ROUTES = {}
def route(name):
    def deco(f): ROUTES[name] = f; return f
    return deco

@route("ping")
def _ping(d): LAST_PING[0] = time.time(); return {"ok": True}

@route("quit")
def _quit(d): threading.Timer(0.3, die).start(); return {"ok": True}

@route("config.get")
def _cfg_get(d):
    return {"ok": True, "cfg": load_cfg(), "defaults": DEFAULTS, "found": {k: bool(find_browser(k)) for k in BROWSERS}, "root": str(ROOT)}

@route("config.set")
def _cfg_set(d):
    c = load_cfg()
    for k in DEFAULTS:
        if k in d and k not in ("projects", "claude"): c[k] = d[k]
    if isinstance(d.get("claude"), dict):
        cl = {f: str(d["claude"].get(f, c["claude"][f])).strip() for f in DEFAULTS["claude"]}
        if cl["base_url"] and not re.match(r"^https?://", cl["base_url"]): raise ValueError("Adres OmniRoute musi zaczynać się od http:// lub https://")
        c["claude"] = cl
    c["db_port"] = int(c["db_port"]); c["apache_port"] = int(c["apache_port"])
    for k, dflt in DEFAULTS["projects"].items():
        src = (d.get("projects") or {}).get(k)
        if not src: continue
        n = {f: src.get(f, c["projects"][k][f]) for f in dflt}
        n["port"] = int(n["port"])
        if not re.match(r"^[\w.-]+$", str(n["folder"])) or n["folder"] in (".", ".."):
            raise ValueError("Niepoprawna nazwa folderu projektu.")
        c["projects"][k] = n
    save_cfg(c)
    return {"ok": True, "cfg": c, "msg": "Zapisano ustawienia"}

@route("db.status")
def _db_status(d):
    c = load_cfg(); up = mysql_up(c); local = []
    if up and bins(c)[0].exists():
        r = sql(c, "SHOW DATABASES")
        if r.returncode == 0: local = [x for x in r.stdout.split() if x not in SYSTEM_DBS]
    dumps = []
    for p in sorted(DUMPS.glob("*.sql")):
        if NAME_RE.match(p.stem):
            s = p.stat(); dumps.append({"name": p.stem, "size": s.st_size, "mtime": s.st_mtime})
    return {"ok": True, "installed": xampp_installed(c), "up": up, "apache": apache_up(c), "local": local, "dumps": dumps,
            "tools": bins(c)[0].exists() and bins(c)[1].exists()}

@route("xampp.install")
def _xampp_install(d):
    c = load_cfg(); path = Path(c["xampp_installer"].strip().strip('"')) if c["xampp_installer"].strip() else None
    if not path:       # bez ustawionej ścieżki szukamy instalatora w Pobranych
        dl = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Downloads"
        path = next(iter(sorted(dl.glob("xampp*.exe"))), None) if dl.exists() else None
    if not path or not path.is_file():
        raise RuntimeError("Nie znaleziono instalatora XAMPP. Wskaż plik .exe w Ustawieniach (Instalator XAMPP).")
    if os.name != "nt": raise RuntimeError("Instalator można uruchomić tylko na Windowsie.")
    os.startfile(str(path))
    return {"ok": True, "msg": f"Uruchomiono instalator: {path.name}"}

@route("svc.start")
def _svc_start(d):
    c = load_cfg(); w = d.get("what"); done = []
    if w in ("mysql", "all") and not mysql_up(c):
        exe = xp(c) / "mysql" / "bin" / "mysqld.exe"
        if not exe.exists(): raise RuntimeError(f"Nie znaleziono mysqld.exe w {exe.parent}")
        spawn([str(exe), f"--defaults-file={exe.parent / 'my.ini'}", "--standalone"], xp(c))
        if not wait_port(c["db_host"], c["db_port"], True): raise RuntimeError("MySQL nie wystartował w 10 s. Uruchom go z panelu XAMPP i sprawdź log.")
        done.append("MySQL")
    if w in ("apache", "all") and not apache_up(c):
        exe = xp(c) / "apache" / "bin" / "httpd.exe"
        if not exe.exists(): raise RuntimeError(f"Nie znaleziono httpd.exe w {exe.parent}")
        spawn([str(exe)], xp(c))
        if not wait_port("127.0.0.1", c["apache_port"], True): raise RuntimeError(f"Apache nie wystartował w 10 s. Czy port {c['apache_port']} nie jest zajęty (Skype, IIS)?")
        done.append("Apache")
    return {"ok": True, "msg": ("Uruchomiono: " + ", ".join(done)) if done else "Już działa"}

@route("svc.stop")
def _svc_stop(d):
    c = load_cfg(); w = d.get("what"); done = []
    if w in ("apache", "all") and apache_up(c):
        run(["taskkill", "/F", "/IM", "httpd.exe"])
        if not wait_port("127.0.0.1", c["apache_port"], False, 6): raise RuntimeError("Nie udało się zatrzymać Apache (może działa jako usługa Windows).")
        done.append("Apache")
    if w in ("mysql", "all") and mysql_up(c):
        adm = xp(c) / "mysql" / "bin" / "mysqladmin.exe"
        if not adm.exists(): raise RuntimeError(f"Nie znaleziono mysqladmin.exe w {adm.parent}")
        r = run([str(adm), *conn(c), "shutdown"])
        if r.returncode: return {"ok": False, "error": "Nie udało się zatrzymać MySQL.", "log": r.stderr.strip()}
        wait_port(c["db_host"], c["db_port"], False, 10); done.append("MySQL")
    return {"ok": True, "msg": ("Zatrzymano: " + ", ".join(done)) if done else "Już zatrzymane"}

@route("db.import")
def _db_import(d):
    c = load_cfg(); name = check_name(d.get("name")); need_mysql(c)
    f = DUMPS / f"{name}.sql"
    if not f.exists(): raise RuntimeError(f"Brak zapisanego pliku {f.name} w folderze DevKit.")
    r = sql(c, f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4")
    if r.returncode: return {"ok": False, "error": "Nie udało się utworzyć bazy.", "log": r.stderr.strip()}
    with open(f, "rb") as fh:
        r = subprocess.run([str(bins(c)[0]), *conn(c), name], stdin=fh, capture_output=True,
                           creationflags=NOWIN, timeout=600)
    if r.returncode: return {"ok": False, "error": "Import nie powiódł się.", "log": r.stderr.decode("utf-8", "replace").strip()}
    return {"ok": True, "msg": f"Zaimportowano bazę {name} ({f.stat().st_size // 1024} KB)"}

@route("db.export")
def _db_export(d):
    c = load_cfg(); name = check_name(d.get("name")); need_mysql(c)
    drop = bool(d.get("drop"))
    DUMPS.mkdir(parents=True, exist_ok=True)
    tmp, dst = DUMPS / f"{name}.sql.tmp", DUMPS / f"{name}.sql"
    r = run([str(bins(c)[1]), *conn(c), "--routines", "--triggers", "--add-drop-database",
             "--default-character-set=utf8mb4", "--databases", name, f"--result-file={tmp}"])
    ok = r.returncode == 0 and tmp.exists() and b"Dump completed" in tmp.read_bytes()[-400:]
    if not ok:
        tmp.unlink(missing_ok=True)
        return {"ok": False, "error": "Eksport nie powiódł się. Baza nie została usunięta.", "log": r.stderr.strip()}
    if dst.exists(): shutil.copy2(dst, DUMPS / f"{name}.prev.sql")   # poprzednia wersja jako kopia
    tmp.replace(dst)
    msg = f"Wyeksportowano {name} ({dst.stat().st_size // 1024} KB)"
    if drop:
        r = sql(c, f"DROP DATABASE `{name}`")
        if r.returncode: return {"ok": False, "error": "Wyeksportowano, ale nie udało się usunąć bazy.", "log": r.stderr.strip()}
        msg += " i usunięto z komputera"
    return {"ok": True, "msg": msg}

@route("data.clear")
def _data_clear(d):
    if claudechat.state()["busy"]: raise RuntimeError("Poczekaj na koniec odpowiedzi Claude albo ją zatrzymaj.")
    n = 0
    for f in list(DUMPS.glob("*")) if DUMPS.exists() else []:
        if f.is_file(): f.unlink(); n += 1
    chat = DATA / "claude-chat.json"
    had_chat = chat.exists(); chat.unlink(missing_ok=True)
    PREVIEW.clear()
    c = load_cfg(); c["db_name"] = ""; c["layouts"] = {}; save_cfg(c)
    return {"ok": True, "msg": f"Wyczyszczono dane: usunięto {n} plików zrzutów" + (" i historię czatu Claude" if had_chat else ""), "cfg": c}

PREVIEW = {}    # ścieżka zrzutu -> (mtime, rozmiar, wynik parsowania)

def _preview(name):
    check_name(name)
    f = DUMPS / f"{name}.sql"
    if not f.exists():
        names = ", ".join(sorted(p.stem for p in DUMPS.glob("*.sql") if NAME_RE.match(p.stem)))
        raise RuntimeError(f"Brak zapisanej bazy „{name}”. Wyeksportuj ją najpierw." + (f" Zapisane: {names}." if names else ""))
    st = f.stat(); hit = PREVIEW.get(str(f))
    if not hit or hit[:2] != (st.st_mtime, st.st_size):
        hit = (st.st_mtime, st.st_size, dumpparse.parse_dump(f.read_text("utf-8", errors="replace")))
        PREVIEW[str(f)] = hit
    return hit[2], st

@route("db.schema")
def _db_schema(d):
    data, st = _preview(d.get("name"))
    if not data["tables"]: raise RuntimeError("W zrzucie nie znaleziono żadnych tabel.")
    return {"ok": True, "tables": data["tables"], "mtime": st.st_mtime, "size": st.st_size,
            "layout": load_cfg()["layouts"].get(d["name"], {})}

@route("db.rows")
def _db_rows(d):
    data, _ = _preview(d.get("name"))
    rows = data["rows"].get(d.get("table"))
    if rows is None: raise RuntimeError("Nie ma takiej tabeli.")
    off, lim = max(0, int(d.get("offset") or 0)), min(500, max(1, int(d.get("limit") or 50)))
    return {"ok": True, "total": len(rows), "rows": rows[off:off + lim]}

@route("db.layout")
def _db_layout(d):
    check_name(d.get("name"))
    c = load_cfg(); lay = d.get("layout") or {}
    c["layouts"][d["name"]] = {str(k): [round(float(v[0])), round(float(v[1]))] for k, v in lay.items() if isinstance(v, list) and len(v) == 2}
    save_cfg(c)
    return {"ok": True}

@route("claude.state")
def _claude_state(d): return {"ok": True, **claudechat.state()}

@route("claude.history")
def _claude_history(d): return {"ok": True, "messages": claudechat.history(), "rev": claudechat.state()["rev"]}

@route("claude.send")
def _claude_send(d): claudechat.send(d.get("text")); return {"ok": True}

@route("claude.stop")
def _claude_stop(d): claudechat.stop(); return {"ok": True, "msg": "Zatrzymuję odpowiedź..."}

@route("claude.clear")
def _claude_clear(d): claudechat.clear(); return {"ok": True, "msg": "Rozpoczęto nową rozmowę"}

@route("claude.models")
def _claude_models(d):
    ids = claudechat.list_models()
    return {"ok": True, "models": ids, "msg": f"Pobrano modele: {len(ids)}"}

@route("link.open")
def _link_open(d): return open_urls([d.get("url", "")])

@route("link.openall")
def _link_all(d): return open_urls([l["url"] for l in load_cfg()["links"]])

@route("db.pma")
def _pma(d):
    port = int(load_cfg()["apache_port"])
    return open_urls([f"http://localhost{'' if port == 80 else ':' + str(port)}/phpmyadmin/"])

def _parse_headers(text):
    h = {}
    for line in (text or "").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            if k.strip(): h[k.strip()] = v.strip()
    return h

def _send(method, url, headers, body, ua, origin, timeout=30):
    if not re.match(r"^https?://", url or ""): raise ValueError("Adres musi zaczynać się od http:// lub https://")
    req = urllib.request.Request(url, data=body.encode("utf-8") if body else None, method=method)
    for k, v in headers.items(): req.add_header(k, v)
    req.add_header("User-Agent", ua or "DevKit/1.0")
    if origin: req.add_header("Origin", origin)
    t = time.time()
    try: resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e: resp = e
    raw = resp.read(2_000_000)
    return resp, raw, int((time.time() - t) * 1000)

@route("req.send")
def _req_send(d):
    resp, raw, ms = _send(d.get("method", "GET"), d.get("url"), _parse_headers(d.get("headers")),
                          d.get("body"), d.get("ua"), d.get("origin"))
    code = resp.status if hasattr(resp, "status") else resp.code
    return {"ok": True, "status": code, "reason": resp.reason, "ms": ms, "size": len(raw),
            "headers": [[k, v] for k, v in resp.headers.items()], "body": raw.decode("utf-8", "replace"),
            "msg": f"{d.get('method', 'GET')} {d.get('url')}: {code} {resp.reason} po {ms} ms"}

@route("req.cors")
def _req_cors(d):
    out = []
    for o in [x.strip() for x in (d.get("origins") or "").splitlines() if x.strip()]:
        try:
            resp, _, ms = _send("OPTIONS", d.get("url"), {"Access-Control-Request-Method": d.get("method", "GET"),
                                "Access-Control-Request-Headers": "content-type,authorization"}, None, d.get("ua"), o)
            allow = resp.headers.get("Access-Control-Allow-Origin")
            code = resp.status if hasattr(resp, "status") else resp.code
            out.append({"origin": o, "status": code, "allow": allow, "allowed": allow in ("*", o)})
        except Exception as e:
            out.append({"origin": o, "status": 0, "allow": None, "allowed": False, "error": str(e)})
    return {"ok": True, "results": out, "msg": f"Sprawdzono {len(out)} originów"}

def _key(d):
    if d.get("key") not in JOBS: raise ValueError("Nieznany projekt")
    return d["key"]

@route("proj.state")
def _proj_state(d):
    k = _key(d); c = load_cfg(); j = JOBS[k]
    state, ext = job_state(k, c)
    if ext and not j.ext_noted:
        j.add(f"[DevKit] Na porcie {proj_port(c['projects'][k])} działa proces uruchomiony poza DevKit (np. z poprzedniej sesji). Zatrzymaj lub zrestartuj, żeby go przejąć.")
    j.ext_noted = ext
    lines, nxt = j.since(int(d.get("since") or 0))
    return {"ok": True, "state": state, "external": ext, "lines": lines, "next": nxt}

@route("proj.create")
def _proj_create(d):
    k = _key(d); c = load_cfg(); p = c["projects"][k]
    if job_state(k, c)[0] != "none": raise RuntimeError("Projekt już istnieje lub jest tworzony.")
    if p["interactive"]:
        if os.name != "nt": raise RuntimeError("Tryb interaktywny działa tylko na Windowsie.")
        proot = ROOT / p["folder"]; cwd = ROOT
        if p["inside"]: proot.mkdir(exist_ok=True); cwd = proot
        DATA.mkdir(parents=True, exist_ok=True)
        bat = DATA / f"tworzenie-{k}.bat"
        chain = p["create"].replace(" --yes", "") + (f" && cd /d \"{proot}\" && {p['post']}" if p["post"].strip() else "")
        bat.write_text(f"@echo off\r\ncd /d \"{cwd}\"\r\n{chain}\r\necho.\r\npause\r\n", "utf-8")
        os.startfile(str(bat))
        return {"ok": True, "msg": "Otwarto okno konsoli. Odpowiedz na pytania kreatora."}
    threading.Thread(target=create_job, args=(k, c), daemon=True).start()
    return {"ok": True, "msg": f"Tworzenie projektu {k}..."}

@route("proj.run")
def _proj_run(d): k = _key(d); start_run(k, load_cfg()); return {"ok": True, "msg": f"Uruchomiono {k}"}

@route("proj.stop")
def _proj_stop(d): k = _key(d); stop_run(k); JOBS[k].add("[DevKit] Zatrzymano."); return {"ok": True, "msg": f"Zatrzymano {k}"}

@route("proj.restart")
def _proj_restart(d):
    k = _key(d); stop_run(k); JOBS[k].add("[DevKit] Restart..."); start_run(k, load_cfg())
    return {"ok": True, "msg": f"Zrestartowano {k}"}

# ---------- serwer ----------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _out(self, code, body, ctype="application/json"):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(b)
    def _host_ok(self): return self.headers.get("Host", "") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")
    def do_GET(self):
        if self._host_ok() and self.path.split("?")[0] in ("/icon.png", "/favicon.ico"):   # ikona okna i paska zadań
            f = next((p for p in (HERE / "ui" / "icon.png", DEVKIT_DIR / "ui" / "icon.png") if p.exists()), None)
            if not f: return self._out(404, "{}")
            b = f.read_bytes()
            self.send_response(200); self.send_header("Content-Type", "image/png"); self.send_header("Content-Length", str(len(b)))
            self.end_headers(); self.wfile.write(b); return
        if not self._host_ok() or self.path.split("?")[0] != "/": return self._out(403, "{}")
        tried = [HERE / "ui" / "index.html", HERE / "index.html", DEVKIT_DIR / "ui" / "index.html", DEVKIT_DIR / "index.html"]
        f = next((p for p in tried if p.exists()), None)
        if not f:
            msg = "<body style='font:15px Segoe UI;background:#15121d;color:#ece8f6;padding:40px'><h2>Brak pliku index.html</h2><p>Umieść go w <b>DevKit\\ui\\index.html</b>. Szukałem w:</p><pre>" + "\n".join(map(str, tried)) + "</pre>"
            return self._out(500, msg, "text/html")
        self._out(200, f.read_text("utf-8").replace("__TOKEN__", TOKEN), "text/html")
    def do_POST(self):
        tok = self.headers.get("X-Token", "")
        if not (self._host_ok() and secrets.compare_digest(tok, TOKEN) and self.path.startswith("/api/")):
            return self._out(403, '{"ok":false,"error":"Brak dostępu"}')
        f = ROUTES.get(self.path[5:])
        if not f: return self._out(404, '{"ok":false,"error":"Nieznana akcja"}')
        try:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            out = f(data)
        except Exception as e:
            out = {"ok": False, "error": str(e) or type(e).__name__}
        self._out(200, json.dumps(out, ensure_ascii=False))

def watchdog():   # zamyka DevKit, gdy okno z interfejsem przestało odpowiadać
    while True:
        time.sleep(5)
        if time.time() - LAST_PING[0] > 120: die()

def main():
    global PORT
    DUMPS.mkdir(parents=True, exist_ok=True)
    claudechat.configure(ROOT, DATA, load_cfg)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    PORT = srv.server_address[1]
    threading.Thread(target=watchdog, daemon=True).start()
    threading.Timer(0.4, lambda: open_app(f"http://127.0.0.1:{PORT}/")).start()
    srv.serve_forever()

if __name__ == "__main__":
    main()
