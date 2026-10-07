import os
import json
import time
import shutil
import subprocess
import threading
import urllib.request
import urllib.error
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ============================================================
# CONFIGURAZIONE GENERALE
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
PROJECTS_DIR = BASE_DIR / "projects"
OUTPUT_DIR = BASE_DIR / "output"
CONFIG_FILE = BASE_DIR / "config.json"

MAVEN = r"C:\maven\bin\mvn.cmd"
HOST = "localhost"
PORT = 12000

# MODELLO PREDEFINITO GRATUITO PER OPENROUTER
DEFAULT_MODEL = "qwen/qwen-2.5-coder-32b-instruct:free"

OPENROUTER_TIMEOUT = 600
MAX_ERROR_LENGTH = 12000
MAX_FILE_LENGTH = 50000

# Lock per evitare chiamate simultanee sovrapposte
AI_LOCK = threading.Lock()

PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# SYSTEM RULES CON PROTOCOLLO STATUS LIVE
# ============================================================

SYSTEM_RULES = r"""
SEI IL MOTORE AI DI UN MINECRAFT PAPER PLUGIN BUILDER.

RUOLO:
Sei un esperto sviluppatore Java specializzato in Minecraft Paper e Maven.

COMUNICAZIONE LIVE DELLO STATO (OBBLIGATORIO):
PRIMA e DURANTE la generazione dei file, devi comunicare in TEMPO REALE all'utente cosa stai facendo inviando righe nel seguente formato ESATTO:

===STATUS=== FASE | Descrizione chiara di cosa stai facendo in italiano

FASI VALIDE DA USARE:
- Thinking (analisi della richiesta o degli errori)
- Analysing (controllo dei file o dipendenze esistenti)
- Planning (decisione architettura, classi, comandi, eventi)
- Editing (preparazione modifica a file esistenti)
- Writing (scrittura di file Java/YAML/POM)
- Checking (verifica import e API Paper)

Esempi obbligatori di status:
===STATUS=== Thinking | Sto analizzando la richiesta del plugin...
===STATUS=== Planning | Sto definendo la struttura del pacchetto e i listener...
===STATUS=== Writing | Sto scrivendo la classe principale del plugin...
===STATUS=== Checking | Verifico che gli import Paper API siano corretti...

FORMATO FILE OBBLIGATORIO:
I file devono essere restituiti ESCLUSIVAMENTE usando questo formato:

===FILE===
percorso/del/file
===CONTENT===
contenuto completo del file
===END FILE===

REGOLE GENERALI:
1. Usa Java e Paper API compatibili.
2. Genera codice realmente compilabile.
3. NON usare markdown code fences (```java o ```json).
4. NON usare pseudocodice o abbreviazioni ("...").
5. Non lasciare codice incompleto o TODO.
6. Mantieni le funzionalità esistenti nei file modificati.
7. Restituisci il contenuto COMPLETO di ogni file modificato/creato.
"""

# ============================================================
# GESTIONE CONFIGURAZIONE (OPENROUTER API KEY & MODEL)
# ============================================================

def load_config():
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def get_api_key():
    config = load_config()
    return config.get("api_key", "").strip()

def get_model():
    config = load_config()
    model = config.get("model", DEFAULT_MODEL).strip()
    return model if model else DEFAULT_MODEL

def update_config(api_key=None, model=None):
    config = load_config()
    if api_key is not None:
        config["api_key"] = api_key.strip()
    if model is not None:
        config["model"] = model.strip()
    save_config(config)

# ============================================================
# OPENROUTER STREAMING ENGINE
# ============================================================

def stream_openrouter(prompt, event_callback):
    """
    Invia la richiesta all'API di OpenRouter (Chat Completions) con gestione avanzata degli errori.
    """
    api_key = get_api_key()
    target_model = get_model()

    if not api_key:
        raise ValueError("Chiave API OpenRouter non configurata. Apri le Impostazioni ed inserisci la tua OpenRouter API Key.")

    event_callback("status", {"phase": "Thinking", "message": f"Connessione ad OpenRouter API ({target_model}) in corso..."})

    with AI_LOCK:
        attempt = 0

        while True:
            attempt += 1
            url = "https://openrouter.ai/api/v1/chat/completions"
            
            payload = {
                "model": target_model,
                "messages": [
                    {"role": "system", "content": SYSTEM_RULES},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.1,
                "stream": True
            }

            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": "http://localhost:12000",
                    "X-Title": "Minecraft Paper Plugin Builder"
                },
                method="POST"
            )

            full_text = []
            line_buffer = ""

            try:
                if attempt > 1:
                    event_callback("status", {
                        "phase": "Thinking", 
                        "message": f"Tentativo API #{attempt} su OpenRouter ({target_model})..."
                    })

                with urllib.request.urlopen(request, timeout=OPENROUTER_TIMEOUT) as response:
                    for raw_line in response:
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line or not line.startswith("data: "):
                            continue

                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break

                        try:
                            data = json.loads(data_str)
                            choices = data.get("choices", [])
                            if not choices:
                                continue
                            
                            delta = choices[0].get("delta", {})
                            token = delta.get("content", "")

                            if token:
                                full_text.append(token)
                                line_buffer += token

                                while "\n" in line_buffer:
                                    sub_line, line_buffer = line_buffer.split("\n", 1)
                                    sub_line_str = sub_line.strip()

                                    if sub_line_str.startswith("===STATUS==="):
                                        content = sub_line_str.replace("===STATUS===", "").strip()
                                        parts_status = content.split("|", 1)
                                        phase = parts_status[0].strip() if len(parts_status) > 0 else "Thinking"
                                        msg = parts_status[1].strip() if len(parts_status) > 1 else ""
                                        event_callback("status", {"phase": phase, "message": msg})

                                    elif sub_line_str.startswith("===FILE==="):
                                        path = sub_line_str.replace("===FILE===", "").strip()
                                        if path:
                                            event_callback("file_detected", {"path": path})

                        except json.JSONDecodeError:
                            continue

                    if line_buffer.strip():
                        rem_str = line_buffer.strip()
                        if rem_str.startswith("===STATUS==="):
                            content = rem_str.replace("===STATUS===", "").strip()
                            parts_status = content.split("|", 1)
                            phase = parts_status[0].strip() if len(parts_status) > 0 else "Thinking"
                            msg = parts_status[1].strip() if len(parts_status) > 1 else ""
                            event_callback("status", {"phase": phase, "message": msg})

                result = "".join(full_text).strip()
                if not result:
                    raise RuntimeError("OpenRouter ha restituito un output vuoto.")
                return result

            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")
                
                # INTERRUZIONE LOOP PER ERRORI DEFINITIVI (Autenticazione / Permessi / Modello Errato)
                if e.code in (401, 403) or "model_not_found" in err_body or "no_models" in err_body:
                    raise RuntimeError(
                        f"Errore OpenRouter API ({e.code}): Il modello '{target_model}' non esiste o la chiave API non è valida.\n"
                        f"Dettaglio: {err_body}"
                    )
                
                if e.code == 429:
                    wait_time = min(10 * attempt, 60)
                else:
                    wait_time = min(2 ** min(attempt, 5) + 1, 10)
                    
                event_callback("status", {
                    "phase": "Thinking", 
                    "message": f"Errore HTTP {e.code} su OpenRouter ({target_model}). Attesa {wait_time}s e nuovo tentativo #{attempt + 1}..."
                })
                time.sleep(wait_time)
                
            except urllib.error.URLError as e:
                wait_time = 5
                is_dns_error = "getaddrinfo failed" in str(e.reason)
                msg = "Impossibile raggiungere openrouter.ai (Verifica la connessione/DNS)." if is_dns_error else f"Errore di rete ({e.reason})."
                
                event_callback("status", {
                    "phase": "Thinking", 
                    "message": f"{msg} Attesa {wait_time}s e nuovo tentativo #{attempt + 1}..."
                })
                time.sleep(wait_time)
                
            except Exception as e:
                if "Errore OpenRouter API" in str(e):
                    raise e
                wait_time = 5
                event_callback("status", {
                    "phase": "Thinking", 
                    "message": f"Errore generico ({e}). Attesa {wait_time}s e nuovo tentativo #{attempt + 1}..."
                })
                time.sleep(wait_time)

def ask_ai_stream(prompt, event_callback):
    return stream_openrouter(prompt, event_callback)

# ============================================================
# PARSER & VALIDATORE FILE
# ============================================================

def parse_file_response(text):
    text = text.replace("\r\n", "\n").strip()
    files = []
    marker = "===FILE==="

    if marker not in text:
        raise ValueError("L'AI non ha incluso il formato file ===FILE=== richiesto.")

    chunks = text.split(marker)
    for chunk in chunks[1:]:
        if "===CONTENT===" not in chunk or "===END FILE===" not in chunk:
            continue

        path_part, content_part = chunk.split("===CONTENT===", 1)
        content_part, _ = content_part.split("===END FILE===", 1)

        rel_path = path_part.strip()
        content = content_part

        if content.startswith("\n"):
            content = content[1:]
        content = content.rstrip()

        if rel_path and content:
            files.append({"path": rel_path, "content": content})

    if not files:
        raise ValueError("Nessun file valido trovato nella risposta dell'AI.")

    return {"files": files}

def validate_relative_path(path):
    path = str(path).strip().replace("\\", "/")
    if not path or path.startswith("/") or ":" in path or ".." in Path(path).parts:
        raise ValueError(f"Percorso file non valido o non sicuro: {path}")
    return path

def write_files(project_dir, data):
    project_root = project_dir.resolve()
    written = []

    for item in data.get("files", []):
        rel_path = validate_relative_path(item["path"])
        file_path = (project_dir / rel_path).resolve()

        if file_path != project_root and project_root not in file_path.parents:
            raise ValueError(f"Accesso negato fuori dal progetto: {rel_path}")

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(item["content"], encoding="utf-8")
        written.append(rel_path)

    return written

def format_project_files(project_dir):
    files = []
    if not project_dir.exists():
        return "NESSUN FILE"

    for path in project_dir.rglob("*"):
        if not path.is_file() or "target" in path.parts or path.suffix.lower() in {".jar", ".class", ".zip"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
            if len(content) > MAX_FILE_LENGTH:
                content = content[:MAX_FILE_LENGTH]
            rel_path = str(path.relative_to(project_dir)).replace("\\", "/")
            files.append(f"===FILE===\n{rel_path}\n===CONTENT===\n{content}\n===END FILE===\n")
        except Exception:
            pass

    return "\n".join(files) if files else "NESSUN FILE"

# ============================================================
# MAVEN & COMPILAZIONE
# ============================================================

def compile_project(project_dir):
    if not (project_dir / "pom.xml").exists():
        return False, "pom.xml non trovato."

    try:
        result = subprocess.run(
            [MAVEN, "clean", "package", "-DskipTests"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600
        )
        output = result.stdout + "\n" + result.stderr
        return (result.returncode == 0), output
    except Exception as e:
        return False, f"Errore durante l'esecuzione di Maven: {e}"

def copy_jar_to_output(project_dir, project_name):
    target_dir = project_dir / "target"
    if not target_dir.exists():
        return False

    jars = [j for j in target_dir.glob("*.jar") if not j.name.endswith(("-sources.jar", "-javadoc.jar"))]
    if not jars:
        return False

    jars.sort(key=lambda p: p.stat().st_size)
    destination = OUTPUT_DIR / f"{project_name}.jar"
    shutil.copy2(jars[0], destination)
    return True

# ============================================================
# WORKFLOW CON INFINITE CORREZIONI AUTO-FIX
# ============================================================

def build_and_fix_stream(project_dir, description, version, event_callback):
    attempt = 0

    while True:
        attempt += 1
        event_callback("status", {
            "phase": "Building",
            "message": f"Compilazione Maven in corso (Tentativo #{attempt})..."
        })

        success, build_output = compile_project(project_dir)

        if success:
            event_callback("status", {
                "phase": "Completed",
                "message": f"Compilazione completata con successo al tentativo #{attempt}! (BUILD SUCCESS)"
            })
            return True, build_output

        current_error = build_output[-MAX_ERROR_LENGTH:]
        error_lines = [line for line in current_error.split("\n") if "[ERROR]" in line]
        short_err = error_lines[0] if error_lines else "Errore generale di compilazione"

        event_callback("status", {
            "phase": "Fixing",
            "message": f"Maven ha rilevato un errore (Tentativo #{attempt}). Avvio correzione con OpenRouter [{get_model()}]...\nDettaglio: {short_err}"
        })

        fix_prompt = f"""
CORREGGI UN PROGETTO MINECRAFT PAPER CHE NON COMPILA.
Versione Minecraft: {version}
Funzionalità: {description}

ERRORE MAVEN:
{current_error}

FILE ATTUALI DEL PROGETTO:
{format_project_files(project_dir)}

Analizza l'errore, trova la causa e restituisci SOLO i file corretti nel formato ===FILE===.
"""
        try:
            raw_response = ask_ai_stream(fix_prompt, event_callback)
            parsed_data = parse_file_response(raw_response)
            write_files(project_dir, parsed_data)
        except Exception as e:
            if "Errore OpenRouter API" in str(e):
                raise e
            event_callback("status", {
                "phase": "Fixing",
                "message": f"Errore nella risposta dell'AI ({e}). Nuovo tentativo in corso..."
            })
            time.sleep(2)

# ============================================================
# API ACTIONS
# ============================================================

def execute_create_plugin(data, event_callback):
    name = data.get("name", "").strip().replace("\\", "_").replace("/", "_")
    author = data.get("author", "Developer")
    version = data.get("version", "1.21")
    description = data.get("description", "")

    if not name:
        raise ValueError("Nome del plugin mancante.")

    project_dir = PROJECTS_DIR / name
    project_dir.mkdir(parents=True, exist_ok=True)

    prompt = f"""
CREA UN NUOVO PLUGIN MINECRAFT PAPER.
Nome: {name}
Autore: {author}
Versione Minecraft: {version}
Funzionalità richieste: {description}

Crea un progetto Maven completo. Genera pom.xml, plugin.yml, pacchetti Java, classe principale, comandi ed eventi.
Usa il formato ===FILE=== per ogni file.
"""
    raw_res = ask_ai_stream(prompt, event_callback)
    files_data = parse_file_response(raw_res)
    write_files(project_dir, files_data)

    success, build_log = build_and_fix_stream(project_dir, description, version, event_callback)

    if success:
        jar_ok = copy_jar_to_output(project_dir, name)
        jar_path = f"output/{name}.jar" if jar_ok else "Non trovato"
        return {"success": True, "message": f"Plugin '{name}' creato con successo!", "jar": jar_path}
    else:
        return {"success": False, "message": "Errore imprevisto.", "log": build_log[-4000:]}

def execute_edit_plugin(data, event_callback):
    name = data.get("name", "").strip()
    description = data.get("description", "")

    project_dir = PROJECTS_DIR / name
    if not project_dir.exists():
        raise ValueError(f"Progetto '{name}' non trovato.")

    prompt = f"""
MODIFICA UN PLUGIN PAPER ESISTENTE.
Nome progetto: {name}
MODIFICA RICHIESTA: {description}

FILE ATTUALI:
{format_project_files(project_dir)}

Implementa la modifica mantenendo le funzionalità esistenti. Restituisci SOLO i file nuovi o modificati in formato ===FILE===.
"""
    raw_res = ask_ai_stream(prompt, event_callback)
    files_data = parse_file_response(raw_res)
    write_files(project_dir, files_data)

    success, build_log = build_and_fix_stream(project_dir, description, "presente nel pom.xml", event_callback)

    if success:
        jar_ok = copy_jar_to_output(project_dir, name)
        jar_path = f"output/{name}.jar" if jar_ok else "Non trovato"
        return {"success": True, "message": f"Plugin '{name}' modificato con successo!", "jar": jar_path}
    else:
        return {"success": False, "message": "Errore imprevisto.", "log": build_log[-4000:]}

# ============================================================
# HTTP SERVER & SSE ENDPOINTS
# ============================================================

class RequestHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML.encode("utf-8"))

        elif self.path == "/api/projects":
            projects = [d.name for d in PROJECTS_DIR.iterdir() if d.is_dir()]
            self.send_json({"projects": projects})

        elif self.path == "/api/config":
            self.send_json({
                "model": get_model(),
                "api_key": get_api_key()
            })

        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        if self.path == "/api/config":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
                update_config(
                    api_key=data.get("api_key"),
                    model=data.get("model")
                )
                self.send_json({"status": "ok", "model": get_model(), "api_key_set": bool(get_api_key())})
            except Exception as e:
                self.send_json({"error": str(e)}, status=400)

        elif self.path == "/api/action":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
            except Exception:
                self.send_error(400, "Invalid JSON")
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            def send_sse(event_type, payload):
                msg = f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                try:
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    pass

            action = data.get("action")
            try:
                if action == "create":
                    res = execute_create_plugin(data, lambda t, p: send_sse(t, p))
                    send_sse("result", res)
                elif action == "edit":
                    res = execute_edit_plugin(data, lambda t, p: send_sse(t, p))
                    send_sse("result", res)
                else:
                    send_sse("result", {"success": False, "message": "Azione non riconosciuta."})
            except Exception as e:
                send_sse("result", {"success": False, "message": str(e)})

        else:
            self.send_error(404, "Not Found")

    def send_json(self, obj, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

# ============================================================
# INTERFACCIA WEB (OPENROUTER EDITION)
# ============================================================

HTML = r"""<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Minecraft OpenRouter Builder</title>
    <style>
        :root {
            --bg-primary: #121214;
            --bg-secondary: #1a1a1e;
            --bg-tertiary: #26262b;
            --accent: #6366f1;
            --accent-hover: #4f46e5;
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --border: #374151;
            --card-bg: #1f1f23;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-primary);
            color: var(--text-main);
            height: 100vh;
            display: flex;
            overflow: hidden;
        }

        .sidebar {
            width: 280px;
            background: var(--bg-secondary);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
            padding: 20px;
        }

        .logo {
            font-size: 1.1rem;
            font-weight: 700;
            color: var(--accent);
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 25px;
        }

        .btn {
            background: var(--accent);
            color: white;
            border: none;
            padding: 10px 15px;
            border-radius: 8px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s;
            width: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }
        .btn:hover { background: var(--accent-hover); }
        .btn-secondary { background: var(--bg-tertiary); color: var(--text-main); border: 1px solid var(--border); }
        .btn-secondary:hover { background: #323238; }

        .project-section {
            margin-top: 20px;
            flex: 1;
            overflow-y: auto;
        }
        .section-title {
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            margin-bottom: 10px;
        }

        .project-list {
            list-style: none;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        .project-item {
            padding: 10px 12px;
            border-radius: 6px;
            background: transparent;
            cursor: pointer;
            border: 1px solid transparent;
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 0.9rem;
        }
        .project-item:hover { background: var(--bg-tertiary); }
        .project-item.active { background: var(--bg-tertiary); border-color: var(--accent); color: var(--accent); }

        .workspace {
            flex: 1;
            display: flex;
            flex-direction: column;
            min-width: 0;
        }

        .header {
            height: 60px;
            border-bottom: 1px solid var(--border);
            padding: 0 25px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: var(--bg-secondary);
        }

        .active-project-badge {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .badge {
            background: var(--bg-tertiary);
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 0.8rem;
            border: 1px solid var(--border);
        }

        .workspace-split {
            flex: 1;
            display: flex;
            min-height: 0;
        }

        .panel-left {
            flex: 1;
            padding: 25px;
            display: flex;
            flex-direction: column;
            gap: 20px;
            overflow-y: auto;
        }

        .form-group {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        label { font-size: 0.85rem; color: var(--text-muted); font-weight: 500; }
        input, select, textarea {
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            color: var(--text-main);
            padding: 12px;
            border-radius: 8px;
            font-family: inherit;
            font-size: 0.95rem;
        }
        input:focus, select:focus, textarea:focus { outline: none; border-color: var(--accent); }
        textarea { resize: vertical; min-height: 120px; }

        .panel-right {
            width: 380px;
            border-left: 1px solid var(--border);
            background: var(--bg-secondary);
            display: flex;
            flex-direction: column;
            padding: 20px;
            gap: 20px;
            overflow-y: auto;
        }

        .status-card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 18px;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        .status-header {
            display: flex;
            align-items: center;
            gap: 10px;
            font-weight: 600;
        }
        .pulse-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: var(--accent);
            box-shadow: 0 0 10px var(--accent);
            animation: pulse 1.5s infinite;
        }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }

        .timeline {
            display: flex;
            flex-direction: column;
            gap: 12px;
            margin-top: 10px;
            position: relative;
        }
        .timeline-item {
            display: flex;
            gap: 12px;
            font-size: 0.85rem;
            position: relative;
        }
        .timeline-badge {
            width: 24px;
            height: 24px;
            border-radius: 50%;
            background: var(--bg-tertiary);
            border: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.7rem;
            flex-shrink: 0;
        }
        .timeline-item.active .timeline-badge { background: var(--accent); color: white; border-color: var(--accent); }
        .timeline-content { flex: 1; }
        .timeline-phase { font-weight: 600; }
        .timeline-msg { color: var(--text-muted); font-size: 0.8rem; margin-top: 2px; }

        .file-list-box {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 12px;
            font-family: monospace;
            font-size: 0.8rem;
            max-height: 150px;
            overflow-y: auto;
        }
        .file-item { color: var(--accent); margin-bottom: 4px; }

        .modal {
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.7);
            align-items: center;
            justify-content: center;
            z-index: 100;
        }
        .modal-content {
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            padding: 25px;
            border-radius: 12px;
            width: 460px;
            display: flex;
            flex-direction: column;
            gap: 15px;
        }
    </style>
</head>
<body>

    <div class="sidebar">
        <div class="logo">
            <span>🌐 OpenRouter Engine</span>
        </div>
        <button class="btn" onclick="setMode('create')">+ Nuovo Plugin</button>

        <div class="project-section">
            <div class="section-title">Progetti Esistenti</div>
            <ul class="project-list" id="projectList">
                <li style="color:var(--text-muted); font-size:0.8rem;">Caricamento...</li>
            </ul>
        </div>

        <button class="btn btn-secondary" onclick="openSettings()">⚙️ Impostazioni OpenRouter</button>
    </div>

    <div class="workspace">
        <div class="header">
            <div class="active-project-badge">
                <h3 id="modeTitle">Nuovo Plugin</h3>
                <span class="badge" id="selectedProjectBadge">Nessun Progetto Selezionato</span>
            </div>
            <span class="badge" id="statusBadge" style="color:var(--accent);">Modello: ...</span>
        </div>

        <div class="workspace-split">
            <div class="panel-left">
                <form id="actionForm" onsubmit="handleSubmit(event)">
                    <div id="createFields">
                        <div class="form-group" style="margin-bottom:15px;">
                            <label>Nome Plugin</label>
                            <input type="text" id="pluginName" placeholder="Es. CustomKits">
                        </div>
                        <div class="form-group" style="margin-bottom:15px;">
                            <label>Autore</label>
                            <input type="text" id="pluginAuthor" value="Developer">
                        </div>
                        <div class="form-group" style="margin-bottom:15px;">
                            <label>Versione Minecraft Target</label>
                            <input type="text" id="pluginVersion" value="1.21">
                        </div>
                    </div>

                    <div class="form-group" style="margin-bottom:20px;">
                        <label id="descLabel">Descrizione Funzionalità Richieste</label>
                        <textarea id="pluginDesc" placeholder="Descrivi in dettaglio cosa deve fare il plugin..."></textarea>
                    </div>

                    <button class="btn" type="submit" id="submitBtn">⚡ Avvia Generazione con OpenRouter</button>
                </form>
            </div>

            <div class="panel-right">
                <div class="status-card">
                    <div class="status-header">
                        <div class="pulse-dot" id="pulseIndicator"></div>
                        <span id="currentPhaseText">In Attesa...</span>
                    </div>
                    <div id="currentStatusMessage" style="font-size:0.85rem; color:var(--text-muted);">
                        Invia una richiesta per iniziare.
                    </div>
                </div>

                <div class="section-title">Timeline Passaggi AI</div>
                <div class="timeline" id="timeline"></div>

                <div class="section-title">File Generati / Modificati</div>
                <div class="file-list-box" id="fileList">
                    <div style="color:var(--text-muted);">Nessun file toccato.</div>
                </div>
            </div>
        </div>
    </div>

    <div class="modal" id="settingsModal">
        <div class="modal-content">
            <h3>Impostazioni OpenRouter API</h3>
            
            <div class="form-group">
                <label>OpenRouter API Key (Ottienila gratuitamente su openrouter.ai)</label>
                <input type="password" id="apiKeyInput" placeholder="sk-or-v1-...">
            </div>

            <div class="form-group">
                <label>Seleziona Modello OpenRouter</label>
                <select id="modelSelect" onchange="toggleCustomModel(this.value)">
                    <option value="qwen/qwen-2.5-coder-32b-instruct:free">Qwen 2.5 Coder 32B (Gratuito - Consigliato)</option>
                    <option value="meta-llama/llama-3.3-70b-instruct:free">Llama 3.3 70B (Gratuito)</option>
                    <option value="deepseek/deepseek-r1:free">DeepSeek R1 (Gratuito)</option>
                    <option value="custom">Personalizzato...</option>
                </select>
            </div>

            <div class="form-group" id="customModelGroup" style="display:none;">
                <label>Nome Modello Personalizzato</label>
                <input type="text" id="customModelInput" placeholder="Es. qwen/qwen-2.5-coder-32b-instruct:free">
            </div>

            <div style="display:flex; gap:10px; justify-content:flex-end; margin-top:10px;">
                <button class="btn btn-secondary" onclick="closeSettings()">Annulla</button>
                <button class="btn" onclick="saveSettings()">Salva Impostazioni</button>
            </div>
        </div>
    </div>

    <script>
        let currentMode = 'create';
        let selectedProject = null;

        async function loadConfigHeader() {
            try {
                const res = await fetch('/api/config');
                const data = await res.json();
                document.getElementById('statusBadge').innerText = `Modello: ${data.model}`;
            } catch(e){}
        }

        async function loadProjects() {
            try {
                const res = await fetch('/api/projects');
                const data = await res.json();
                const list = document.getElementById('projectList');
                list.innerHTML = '';
                
                if(!data.projects || data.projects.length === 0) {
                    list.innerHTML = '<li style="color:var(--text-muted); font-size:0.8rem;">Nessun progetto trovato.</li>';
                    return;
                }

                data.projects.forEach(p => {
                    const li = document.createElement('li');
                    li.className = `project-item ${selectedProject === p ? 'active' : ''}`;
                    li.innerText = p;
                    li.onclick = () => selectProject(p);
                    list.appendChild(li);
                });
            } catch (e) {
                console.error(e);
            }
        }

        function setMode(mode) {
            currentMode = mode;
            document.getElementById('createFields').style.display = mode === 'create' ? 'block' : 'none';
            document.getElementById('modeTitle').innerText = mode === 'create' ? 'Nuovo Plugin' : 'Modifica Plugin';
            document.getElementById('descLabel').innerText = mode === 'create' ? 'Descrizione Funzionalità' : 'Modifiche da Apportare';
            document.getElementById('submitBtn').innerText = mode === 'create' ? '⚡ Avvia Generazione AI' : '✏️ Applica Modifiche AI';
            
            if(mode === 'create') {
                selectedProject = null;
                document.getElementById('selectedProjectBadge').innerText = 'Nessun Progetto Selezionato';
                loadProjects();
            }
        }

        function selectProject(name) {
            selectedProject = name;
            setMode('edit');
            document.getElementById('selectedProjectBadge').innerText = `Selezionato: ${name}`;
            loadProjects();
        }

        function toggleCustomModel(val) {
            const customGroup = document.getElementById('customModelGroup');
            customGroup.style.display = val === 'custom' ? 'flex' : 'none';
        }

        async function openSettings() {
            const res = await fetch('/api/config');
            const data = await res.json();
            document.getElementById('apiKeyInput').value = data.api_key || '';
            
            const modelSelect = document.getElementById('modelSelect');
            const model = data.model || 'qwen/qwen-2.5-coder-32b-instruct:free';
            
            if (['qwen/qwen-2.5-coder-32b-instruct:free', 'meta-llama/llama-3.3-70b-instruct:free', 'deepseek/deepseek-r1:free'].includes(model)) {
                modelSelect.value = model;
                toggleCustomModel(model);
            } else {
                modelSelect.value = 'custom';
                toggleCustomModel('custom');
                document.getElementById('customModelInput').value = model;
            }

            document.getElementById('settingsModal').style.display = 'flex';
        }

        function closeSettings() {
            document.getElementById('settingsModal').style.display = 'none';
        }

        async function saveSettings() {
            const apiKey = document.getElementById('apiKeyInput').value;
            const selectVal = document.getElementById('modelSelect').value;
            let selectedModel = selectVal;

            if (selectVal === 'custom') {
                selectedModel = document.getElementById('customModelInput').value.trim();
            }

            if(!selectedModel) selectedModel = 'qwen/qwen-2.5-coder-32b-instruct:free';

            await fetch('/api/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({api_key: apiKey, model: selectedModel})
            });

            closeSettings();
            loadConfigHeader();
        }

        function addTimelineStep(phase, message) {
            const timeline = document.getElementById('timeline');
            const item = document.createElement('div');
            item.className = 'timeline-item active';
            item.innerHTML = `
                <div class="timeline-badge">✓</div>
                <div class="timeline-content">
                    <div class="timeline-phase">${phase}</div>
                    <div class="timeline-msg">${message}</div>
                </div>
            `;
            timeline.appendChild(item);
            timeline.scrollTop = timeline.scrollHeight;
        }

        function addFileToList(path) {
            const box = document.getElementById('fileList');
            if(box.innerText.includes('Nessun file toccato.')) box.innerHTML = '';
            const div = document.createElement('div');
            div.className = 'file-item';
            div.innerText = `📄 ${path}`;
            box.appendChild(div);
        }

        async function handleSubmit(e) {
            e.preventDefault();
            
            const submitBtn = document.getElementById('submitBtn');
            submitBtn.disabled = true;
            
            document.getElementById('timeline').innerHTML = '';
            document.getElementById('fileList').innerHTML = '<div style="color:var(--text-muted);">In attesa file...</div>';

            const payload = {
                action: currentMode,
                name: currentMode === 'create' ? document.getElementById('pluginName').value : selectedProject,
                author: document.getElementById('pluginAuthor').value,
                version: document.getElementById('pluginVersion').value,
                description: document.getElementById('pluginDesc').value
            };

            try {
                const response = await fetch('/api/action', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n\n');
                    buffer = lines.pop();

                    for (const chunk of lines) {
                        if (!chunk.trim()) continue;
                        
                        let eventType = 'message';
                        let dataStr = '';

                        chunk.split('\n').forEach(line => {
                            if (line.indexOf('event: ') === 0) eventType = line.replace('event: ', '').trim();
                            if (line.indexOf('data: ') === 0) dataStr = line.replace('data: ', '');
                        });

                        if (dataStr) {
                            try {
                                const data = JSON.parse(dataStr);
                                
                                if (eventType === 'status') {
                                    document.getElementById('currentPhaseText').innerText = data.phase;
                                    document.getElementById('currentStatusMessage').innerText = data.message;
                                    addTimelineStep(data.phase, data.message);
                                } 
                                else if (eventType === 'file_detected') {
                                    addFileToList(data.path);
                                }
                                else if (eventType === 'result') {
                                    submitBtn.disabled = false;
                                    if (data.success) {
                                        alert(data.message);
                                        loadProjects();
                                    } else {
                                        alert('Errore: ' + data.message);
                                    }
                                }
                            } catch (err) {
                                console.error(err);
                            }
                        }
                    }
                }
            } catch (err) {
                alert("Errore di connessione: " + err.message);
                submitBtn.disabled = false;
            }
        }

        loadProjects();
        loadConfigHeader();
    </script>
</body>
</html>
"""

# ============================================================
# MAIN ENTRYPOINT
# ============================================================

def run_server():
    server = ThreadingHTTPServer((HOST, PORT), RequestHandler)
    print("=" * 60)
    print(f"⚡ MINECRAFT BUILDER ATTIVO SU OPENROUTER [{get_model()}]")
    print(f"🌐 APRI L'INTERFACCIA SU http://{HOST}:{PORT}")
    print("=" * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArresto del server...")
        server.server_close()

if __name__ == "__main__":
    run_server()
