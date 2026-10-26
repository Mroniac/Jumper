#!/usr/bin/env python3
"""Jumper — move code and files (up to 100 MB) between your PC and phone over home Wi-Fi.
Jumper — передача кода и файлов между ПК и телефоном по домашней Wi-Fi сети.

Run / Запуск:  python jumper.py   (or Jumper.exe)
The web page is embedded in this file. / Страница встроена в этот файл.
"""
from urllib.parse import unquote, quote
import json, re, os, socket, sys, threading, time, uuid, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

PORT = 8765
DISCOVERY_PORT = 8766  # приложение Jumper на телефоне ищет ПК через этот порт
MAX_CLIPS = 60
# В собранном exe данные лежат рядом с exe, а не во временной папке
HERE = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
STORE = os.path.join(HERE, "jumper_clips.json")
FILES_DIR = os.path.join(HERE, "jumper_files")
MAX_FILE = 100 * 1024 * 1024
os.makedirs(FILES_DIR, exist_ok=True)
lock = threading.Lock()


def system_is_russian():
    try:
        if sys.platform == "win32":
            import ctypes
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return (lang_id & 0x3FF) in (0x19, 0x22, 0x23, 0x3F)  # ru, uk, be, kk
        import locale
        return (locale.getlocale()[0] or os.environ.get("LANG", "")).lower().startswith(("ru", "uk", "be", "kk"))
    except Exception:
        return False


RU = system_is_russian()
MSG = {
    "no_discovery": ("Автопоиск не запустился (порт 8766 занят) — в приложении введите адрес вручную.",
                     "Auto-discovery is off (port 8766 is busy). Enter the address manually in the app."),
    "started": ("Jumper запущен.", "Jumper is running."),
    "this_pc": ("  На этом ПК:    ", "  On this PC:    "),
    "phone": ("  На телефоне:   ", "  On your phone: "),
    "hint": ("Телефон и ПК должны быть в одной Wi-Fi сети. Остановить: Ctrl+C",
             "Phone and PC must be on the same Wi-Fi network. Stop: Ctrl+C"),
    "busy": ("Порт %d занят — похоже, Jumper уже запущен.", "Port %d is busy. Jumper is probably already running."),
    "enter": ("Нажмите Enter, чтобы закрыть окно.", "Press Enter to close this window."),
}


def msg(key):
    return MSG[key][0 if RU else 1]


def load():
    try:
        with open(STORE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save(clips):
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(clips, f, ensure_ascii=False)
    os.replace(tmp, STORE)


clips = load()


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def drop_file(clip):
    if clip.get("kind") == "file":
        try:
            os.remove(os.path.join(FILES_DIR, clip["id"]))
        except OSError:
            pass


def add_clip(clip):
    with lock:
        clips.insert(0, clip)
        for old in clips[MAX_CLIPS:]:
            drop_file(old)
        del clips[MAX_CLIPS:]
        save(clips)


def discovery_responder():
    """Отвечает телефону на широковещательный запрос «где Jumper?»."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", DISCOVERY_PORT))
    except OSError:
        print(msg("no_discovery"))
        return
    reply = ("JUMPER_HERE %d %s" % (PORT, socket.gethostname())).encode("utf-8")
    while True:
        try:
            data, addr = sock.recvfrom(256)
            if data.strip() == b"JUMPER_DISCOVER":
                sock.sendto(reply, addr)
        except OSError:
            time.sleep(0.5)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body=b"", ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html", "/jumper.html"):
            self.send(200, PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path.startswith("/api/files/"):
            cid = self.path.rsplit("/", 1)[-1]
            with lock:
                clip = next((c for c in clips if c["id"] == cid and c.get("kind") == "file"), None)
            path = os.path.join(FILES_DIR, cid)
            if not clip or not re.fullmatch(r"[0-9a-f]{12}", cid) or not os.path.exists(path):
                return self.send(404)
            size = os.path.getsize(path)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(clip["name"]))
            self.end_headers()
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(1024 * 256)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        elif self.path == "/api/clips":
            with lock:
                body = json.dumps(clips, ensure_ascii=False).encode()
            self.send(200, body)
        else:
            self.send(404)

    def do_POST(self):
        if self.path == "/api/files":
            return self.upload()
        if self.path != "/api/clips":
            return self.send(404)
        n = int(self.headers.get("Content-Length", 0))
        if n > 2_000_000:
            return self.send(413)
        try:
            data = json.loads(self.rfile.read(n))
            text = str(data["text"])
        except Exception:
            return self.send(400)
        clip = {
            "id": uuid.uuid4().hex[:12],
            "text": text,
            "device": str(data.get("device", ""))[:20],
            "createdAt": int(time.time() * 1000),
        }
        add_clip(clip)
        self.send(201, b"{}")

    def upload(self):
        n = int(self.headers.get("Content-Length", 0))
        if n > MAX_FILE:
            return self.send(413)
        name = os.path.basename(unquote(self.headers.get("X-Filename", "file")).replace("\\", "/")) or "file"
        cid = uuid.uuid4().hex[:12]
        path = os.path.join(FILES_DIR, cid)
        left = n
        try:
            with open(path, "wb") as f:
                while left > 0:
                    chunk = self.rfile.read(min(left, 1024 * 256))
                    if not chunk:
                        raise IOError("connection closed")
                    f.write(chunk)
                    left -= len(chunk)
        except Exception:
            try:
                os.remove(path)
            except OSError:
                pass
            return self.send(400)
        add_clip({
            "id": cid, "kind": "file", "name": name[:200], "size": n,
            "text": "", "device": unquote(self.headers.get("X-Device", ""))[:20],
            "createdAt": int(time.time() * 1000),
        })
        self.send(201, b"{}")

    def do_DELETE(self):
        if not self.path.startswith("/api/clips/"):
            return self.send(404)
        cid = self.path.rsplit("/", 1)[-1]
        with lock:
            for c in clips:
                if c["id"] == cid:
                    drop_file(c)
            clips[:] = [c for c in clips if c["id"] != cid]
            save(clips)
        self.send(200, b"{}")


PAGE_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Jumper</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Onest:wght@400;500;600&family=Unbounded:wght@600&family=JetBrains+Mono:wght@400;600&display=swap">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<style>
/* Layout: one column — composer on top, a live feed of code clips below, newest first */
:root{
  --bg:#EEF1F6; --panel:#FFFFFF; --code:#F6F8FB; --ink:#18202E; --muted:#5E6A80; --line:#D7DDE8;
  --accent:#2C55E0; --accent-ink:#FFFFFF; --ok:#1F8A4C; --danger:#C2373B;
  --k:#8A2DC2; --s:#1F7A3E; --n:#B25E00; --c:#7D879A; --f:#2C55E0; --t:#0B7C8C;
  --f-ui:"Onest",system-ui,-apple-system,"Segoe UI",sans-serif;
  --f-brand:"Unbounded","Onest",system-ui,sans-serif;
  --f-code:"JetBrains Mono",ui-monospace,"SFMono-Regular",Consolas,monospace;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#0F131B; --panel:#171C27; --code:#11151E; --ink:#E4E8F0; --muted:#8B95A8; --line:#283043;
  --accent:#6F8DFF; --accent-ink:#0B0F18; --ok:#4CC584; --danger:#F07A7D;
  --k:#C792EA; --s:#A5D68A; --n:#F2A65A; --c:#6C778C; --f:#82AAFF; --t:#64D3DE; color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#0F131B; --panel:#171C27; --code:#11151E; --ink:#E4E8F0; --muted:#8B95A8; --line:#283043;
  --accent:#6F8DFF; --accent-ink:#0B0F18; --ok:#4CC584; --danger:#F07A7D;
  --k:#C792EA; --s:#A5D68A; --n:#F2A65A; --c:#6C778C; --f:#82AAFF; --t:#64D3DE; color-scheme:dark}
html,body{background:var(--bg)}
body{color:var(--ink);font-family:var(--f-ui);font-size:15px;line-height:1.5;
  padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));padding-block:20px 40px}
.wrap{max-width:860px;margin:0 auto;display:flex;flex-direction:column;gap:18px}
header{display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap}
.brand{font-family:var(--f-brand);font-size:22px;letter-spacing:.02em;margin:0}
.brand span{color:var(--accent)}
.status{font-size:13px;color:var(--muted);display:flex;align-items:center;gap:8px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--muted)}
.dot.on{background:var(--ok)}
.composer{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px;display:flex;flex-direction:column;gap:10px}
textarea{width:100%;box-sizing:border-box;min-height:140px;resize:vertical;border:1px solid var(--line);border-radius:8px;
  background:var(--code);color:var(--ink);font-family:var(--f-code);font-size:13px;line-height:1.55;padding:10px 12px;tab-size:2}
textarea:focus{outline:2px solid var(--accent);outline-offset:-1px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.hint{font-size:12px;color:var(--muted);flex:1;min-width:160px}
button{font-family:var(--f-ui);font-size:14px;font-weight:600;border-radius:8px;border:1px solid var(--line);
  background:var(--panel);color:var(--ink);padding:9px 14px;cursor:pointer;min-height:40px}
button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
button.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}
button.ghost{border-color:transparent;background:transparent;color:var(--muted);padding:9px 10px}
button.ghost:hover{color:var(--danger)}
button:disabled{opacity:.5;cursor:default}
.feed{display:flex;flex-direction:column;gap:14px}
.clip{background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden;min-width:0}
.clip.fresh{border-color:var(--accent)}
.bar{display:flex;align-items:center;gap:10px;padding:8px 8px 8px 14px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.meta{font-size:12px;color:var(--muted);flex:1;min-width:0;display:flex;gap:8px;flex-wrap:wrap;align-items:center;font-variant-numeric:tabular-nums}
.lang{font-family:var(--f-code);font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--ink);
  border:1px solid var(--line);border-radius:4px;padding:1px 6px}
.copy.done{background:var(--ok);border-color:var(--ok);color:#fff}
.codebox{overflow-x:auto;background:var(--code);max-height:520px;overflow-y:auto}
.codebox.open{max-height:none}
pre{margin:0;display:flex;font-family:var(--f-code);font-size:13px;line-height:1.6}
.ln{user-select:none;text-align:right;color:var(--c);padding:10px 10px 10px 12px;border-right:1px solid var(--line);white-space:pre}
code{display:block;padding:10px 14px;white-space:pre;color:var(--ink)}
.more{width:100%;border:0;border-top:1px solid var(--line);border-radius:0;color:var(--accent);background:var(--panel);font-size:13px}
.filebar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding-top:10px;border-top:1px solid var(--line)}
.drop{border:2px dashed var(--accent)!important}
.progress{flex:1;min-width:140px;height:8px;background:var(--code);border:1px solid var(--line);border-radius:4px;overflow:hidden}
.progress i{display:block;height:100%;width:0;background:var(--accent)}
.fileclip .fbody{display:flex;align-items:center;gap:12px;padding:14px;min-width:0}
.fname{font-family:var(--f-code);font-size:14px;word-break:break-all;flex:1;min-width:0}
a.btn{font-family:var(--f-ui);font-size:14px;font-weight:600;border-radius:8px;background:var(--accent);color:var(--accent-ink);
  padding:9px 14px;text-decoration:none;min-height:22px;display:inline-flex;align-items:center}
.empty{text-align:center;color:var(--muted);padding:36px 16px;border:1px dashed var(--line);border-radius:12px}
.toast{position:fixed;left:50%;transform:translateX(-50%);bottom:calc(20px + env(safe-area-inset-bottom,0px));
  background:var(--ink);color:var(--bg);padding:10px 16px;border-radius:8px;font-size:14px;z-index:20}
.notice{font-size:13px;color:var(--muted);background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
/* syntax tokens */
.hljs-keyword,.hljs-selector-tag,.hljs-built_in,.hljs-literal{color:var(--k)}
.hljs-string,.hljs-regexp,.hljs-attr,.hljs-template-tag{color:var(--s)}
.hljs-number,.hljs-symbol{color:var(--n)}
.hljs-comment,.hljs-quote,.hljs-meta{color:var(--c);font-style:italic}
.hljs-title,.hljs-title.function_,.hljs-section{color:var(--f)}
.hljs-type,.hljs-title.class_,.hljs-tag,.hljs-name,.hljs-attribute,.hljs-variable,.hljs-params{color:var(--t)}
@media (prefers-reduced-motion:no-preference){button{transition:background .15s,color .15s}}
</style>
</head><body>
<div class="wrap">
  <header>
    <h1 class="brand">JUMP<span>ER</span></h1>
    <div class="status"><span class="dot" id="dot"></span><span id="statusText" data-i18n="connecting">Подключение…</span></div>
  </header>

  <div class="notice" id="notice" hidden></div>

  <section class="composer">
    <label for="input" class="hint" style="flex:none" data-i18n="lead">Вставьте код — он сразу появится на всех ваших устройствах</label>
    <textarea id="input" spellcheck="false" autocapitalize="off" autocomplete="off" placeholder="// вставьте код сюда" data-i18n-ph="placeholder"></textarea>
    <div class="row">
      <span class="hint" data-i18n="ctrlEnter">Ctrl + Enter — отправить</span>
      <button id="clear" type="button" data-i18n="clear">Очистить</button>
      <button id="paste" type="button" data-i18n="paste">Вставить из буфера</button>
      <button id="send" class="primary" type="button" data-i18n="send">Отправить</button>
    </div>
    <div class="filebar">
      <input type="file" id="fileInput" multiple hidden>
      <button id="pickFile" type="button" data-i18n="sendFile">Отправить файл</button>
      <span class="hint" id="fileHint" data-i18n="fileHint">До 100 МБ. На ПК можно перетащить файл в это окно.</span>
      <div class="progress" id="prog" hidden><i></i></div>
    </div>
  </section>

  <section class="feed" id="feed"></section>
</div>
<div class="toast" id="toast" hidden></div>

<script>
const $ = s => document.querySelector(s);
const feed = $("#feed"), input = $("#input"), sendBtn = $("#send");
const APP = window.JumperApp || null; // мост из Android-приложения Jumper

// ---- Языки: русский для русской системы, иначе английский ----
const LANG = /^ru|^uk|^be|^kk/i.test(navigator.language || "") ? "ru" : "en";
const I18N = {
  ru: {
    connecting:"Подключение…", lead:"Вставьте код — он сразу появится на всех ваших устройствах", placeholder:"// вставьте код сюда",
    ctrlEnter:"Ctrl + Enter — отправить", clear:"Очистить", paste:"Вставить из буфера", send:"Отправить", sendFile:"Отправить файл",
    fileHint:"До 100 МБ. На ПК можно перетащить файл в это окно.", pc:"ПК", phone:"Телефон", today:"сегодня",
    copied:"Скопировано", copyAll:"Скопировать всё", copyFail:"Не удалось скопировать — выделите код вручную",
    empty:"Пока пусто. Отправьте код — он появится здесь и на телефоне. Ниже пример.", file:"файл", del:"Удалить", download:"Скачать",
    lines:"стр.", sample:"пример", showAll:"Показать весь код", collapse:"Свернуть",
    needCode:"Сначала вставьте код", tooBig:"Слишком большой фрагмент (больше 200 000 символов)", sent:"Отправлено",
    quota:"Хранилище заполнено — удалите старые фрагменты", sendFail:"Не отправилось, попробуйте ещё раз", delFail:"Не удалось удалить",
    b:"Б", kb:"КБ", mb:"МБ", filesNeedServer:"Файлы передаются только через jumper.py", waitUpload:"Дождитесь окончания загрузки",
    over100:"«{0}» больше 100 МБ", sending:"Отправка {0} из {1}: {2}", filesSent:"Файлы отправлены", fileSent:"Файл отправлен",
    fileTooBig:"Файл больше 100 МБ", fileFail:"Не удалось отправить файл", localOnly:"Только это устройство",
    holdPaste:"Удерживайте палец в поле и выберите «Вставить»", pressCtrlV:"Нажмите Ctrl+V", clipEmpty:"Буфер обмена пуст", pasted:"Вставлено",
    synced:"Синхронизировано", noPc:"Нет связи с ПК", newFrom:"Новый код с устройства «{0}»",
    noServer:"Не вижу jumper.py. Проверьте, что он запущен на ПК и оба устройства в одной Wi-Fi сети.",
    direct:"Файл открыт напрямую, поэтому код сохраняется только на этом устройстве. Для передачи на телефон запустите jumper.py и откройте адрес, который он покажет.",
    sampleCode:"// Пример: так будет выглядеть ваш код\nfunction greet(name) {\n  const hour = new Date().getHours();\n  return hour < 12 ? `Доброе утро, ${name}!` : `Привет, ${name}!`;\n}\n\nconsole.log(greet(\"Jumper\"));"
  },
  en: {
    connecting:"Connecting…", lead:"Paste code here and it shows up on all your devices right away", placeholder:"// paste code here",
    ctrlEnter:"Ctrl + Enter to send", clear:"Clear", paste:"Paste from clipboard", send:"Send", sendFile:"Send file",
    fileHint:"Up to 100 MB. On a PC you can drop a file onto this window.", pc:"PC", phone:"Phone", today:"today",
    copied:"Copied", copyAll:"Copy all", copyFail:"Couldn't copy. Select the code manually",
    empty:"Nothing here yet. Send some code and it appears here and on your phone. Example below.", file:"file", del:"Delete", download:"Download",
    lines:"lines", sample:"example", showAll:"Show all code", collapse:"Collapse",
    needCode:"Paste some code first", tooBig:"Snippet is too large (over 200,000 characters)", sent:"Sent",
    quota:"Storage is full. Delete old snippets", sendFail:"Not sent, please try again", delFail:"Couldn't delete",
    b:"B", kb:"KB", mb:"MB", filesNeedServer:"Files can only be sent through jumper.py", waitUpload:"Wait for the current upload to finish",
    over100:"“{0}” is larger than 100 MB", sending:"Sending {0} of {1}: {2}", filesSent:"Files sent", fileSent:"File sent",
    fileTooBig:"File is larger than 100 MB", fileFail:"Couldn't send the file", localOnly:"This device only",
    holdPaste:"Long-press the field and choose Paste", pressCtrlV:"Press Ctrl+V", clipEmpty:"Clipboard is empty", pasted:"Pasted",
    synced:"Synced", noPc:"PC not reachable", newFrom:"New code from {0}",
    noServer:"Can't reach jumper.py. Make sure it's running on your PC and both devices are on the same Wi-Fi.",
    direct:"This file was opened directly, so code is saved on this device only. To reach your phone, run jumper.py and open the address it shows.",
    sampleCode:"// Example: this is how your code will look\nfunction greet(name) {\n  const hour = new Date().getHours();\n  return hour < 12 ? `Good morning, ${name}!` : `Hello, ${name}!`;\n}\n\nconsole.log(greet(\"Jumper\"));"
  }
};
const T = (k, ...a) => (I18N[LANG][k] || I18N.ru[k] || k).replace(/\{(\d)\}/g, (_, i) => a[i]);
document.documentElement.lang = LANG;
document.querySelectorAll("[data-i18n]").forEach(el => el.textContent = T(el.dataset.i18n));
document.querySelectorAll("[data-i18n-ph]").forEach(el => el.placeholder = T(el.dataset.i18nPh));
document.title = "Jumper";

// Устройство хранится кодом ("pc"/"phone"), старые записи — по-русски
const devCode = d => (d === "phone" || d === "Телефон") ? "phone" : (d === "pc" || d === "ПК") ? "pc" : (d || "");
const devName = d => { const c = devCode(d); return c === "phone" || c === "pc" ? T(c) : c; };
const DEVICE = APP || /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent) ? "phone" : "pc";
const MAX_CLIPS = 60;
let col = null, clips = [], lastSeen = null, localMode = false;
const SAMPLE = [{id:"sample",text:T("sampleCode"),device:"pc",createdAt:Date.now(),sample:true}];

function toast(msg){const t=$("#toast");t.textContent=msg;t.hidden=false;clearTimeout(toast.t);toast.t=setTimeout(()=>t.hidden=true,1800)}
function setStatus(on,text){$("#dot").classList.toggle("on",on);$("#statusText").textContent=text}
function fmt(ts){const d=new Date(ts),n=new Date();const hm=d.toLocaleTimeString(LANG==="ru"?"ru-RU":undefined,{hour:"2-digit",minute:"2-digit"});
  return d.toDateString()===n.toDateString()?T("today")+", "+hm:d.toLocaleDateString(LANG==="ru"?"ru-RU":undefined,{day:"numeric",month:"short"})+", "+hm}
function esc(s){return s.replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]))}

function highlight(text){
  if (window.hljs) { try { const r = hljs.highlightAuto(text); return {html:r.value, lang:r.language||"text"}; } catch(e){} }
  return {html:esc(text), lang:"text"};
}

async function copyText(text, btn){
  let ok=false;
  if(APP){ try{ APP.copy(text); ok=true; }catch(e){} }
  if(!ok) try{ await navigator.clipboard.writeText(text); ok=true; }catch(e){
    const ta=document.createElement("textarea");ta.value=text;ta.style.position="fixed";ta.style.opacity="0";document.body.appendChild(ta);
    ta.select();try{ok=document.execCommand("copy")}catch(_){}ta.remove();
  }
  if(ok){btn.textContent=T("copied");btn.classList.add("done");setTimeout(()=>{btn.textContent=T("copyAll");btn.classList.remove("done")},1600)}
  else toast(T("copyFail"));
}

function render(){
  feed.innerHTML="";
  const list = clips.length ? clips : SAMPLE;
  if(!clips.length){const e=document.createElement("div");e.className="empty";e.textContent=T("empty");feed.appendChild(e)}
  list.forEach((c,i)=>{
    if(c.kind==="file"){
      const el=document.createElement("article");el.className="clip fileclip"+(i===0&&c.id===lastSeen?" fresh":"");
      el.innerHTML=`<div class="bar"><div class="meta"><span class="lang">${T("file")}</span><span class="who"></span><span class="sz"></span></div>
        <button class="ghost del" type="button">${T("del")}</button></div>
        <div class="fbody"><span class="fname"></span><a class="btn" download>${T("download")}</a></div>`;
      el.querySelector(".who").textContent=devName(c.device)+" · "+fmt(c.createdAt);
      el.querySelector(".sz").textContent=fmtSize(c.size);
      el.querySelector(".fname").textContent=c.name;
      const a=el.querySelector("a");a.href="/api/files/"+encodeURIComponent(c.id);a.setAttribute("download",c.name);
      el.querySelector(".del").onclick=()=>removeClip(c.id);
      feed.appendChild(el);return;
    }
    const {html,lang}=highlight(c.text);
    const lines=c.text.split("\n").length;
    const el=document.createElement("article");el.className="clip"+(i===0&&clips.length&&c.id===lastSeen?" fresh":"");
    el.innerHTML=`<div class="bar"><div class="meta"><span class="lang"></span><span class="who"></span><span>${lines} ${T("lines")}</span></div>
      <button class="ghost del" type="button">${T("del")}</button><button class="copy primary" type="button">${T("copyAll")}</button></div>
      <div class="codebox"><pre><div class="ln"></div><code></code></pre></div>`;
    el.querySelector(".lang").textContent=c.sample?T("sample"):lang;
    el.querySelector(".who").textContent=devName(c.device)+" · "+fmt(c.createdAt);
    el.querySelector(".ln").textContent=Array.from({length:lines},(_,k)=>k+1).join("\n");
    el.querySelector("code").innerHTML=html;
    el.querySelector(".copy").onclick=e=>copyText(c.text,e.currentTarget);
    const del=el.querySelector(".del");
    if(c.sample) del.hidden=true; else del.onclick=()=>removeClip(c.id);
    if(lines>30){const box=el.querySelector(".codebox");const m=document.createElement("button");m.className="more";m.type="button";m.textContent=T("showAll");
      m.onclick=()=>{box.classList.toggle("open");m.textContent=box.classList.contains("open")?T("collapse"):T("showAll")};el.appendChild(m)}
    feed.appendChild(el);
  });
}

async function send(){
  const text=input.value.replace(/\s+$/,"");
  if(!text.trim()){toast(T("needCode"));return}
  if(text.length>200000){toast(T("tooBig"));return}
  sendBtn.disabled=true;
  const clip={text,device:DEVICE,createdAt:Date.now()};
  try{
    if(col){ await col.add(clip); }
    else { clip.id="l"+clip.createdAt; clips.unshift(clip); saveLocal(); render(); }
    input.value=""; try{localStorage.removeItem("jumper-draft")}catch(e){}
    toast(T("sent"));
    pruneOld();
  }catch(e){
    toast(e&&e.code==="quota_exceeded"?T("quota"):T("sendFail"));
  }finally{sendBtn.disabled=false}
}

async function removeClip(id){
  if(col){ try{await col.doc(id).delete()}catch(e){toast(T("delFail"))} }
  else { clips=clips.filter(c=>c.id!==id); saveLocal(); render(); }
}
async function pruneOld(){
  if(!col||clips.length<=MAX_CLIPS) return;
  for(const c of clips.slice(MAX_CLIPS)){ try{await col.doc(c.id).delete()}catch(e){} }
}
function saveLocal(){try{localStorage.setItem("jumper-local",JSON.stringify(clips.slice(0,MAX_CLIPS)))}catch(e){}}
function loadLocal(){try{clips=JSON.parse(localStorage.getItem("jumper-local")||"[]")}catch(e){clips=[]}}

function fmtSize(b){if(b<1024)return b+" "+T("b");if(b<1048576)return (b/1024).toFixed(0)+" "+T("kb");const m=(b/1048576).toFixed(1);return (LANG==="ru"?m.replace(".",","):m)+" "+T("mb")}
const MAX_FILE=100*1024*1024;
let uploading=false;
function uploadOne(file){
  return new Promise((resolve,reject)=>{
    const x=new XMLHttpRequest();
    x.open("POST","/api/files");
    x.setRequestHeader("X-Filename",encodeURIComponent(file.name));
    x.setRequestHeader("X-Device",encodeURIComponent(DEVICE));
    x.upload.onprogress=e=>{if(e.lengthComputable)$("#prog i").style.width=(e.loaded/e.total*100)+"%"};
    x.onload=()=>x.status<300?resolve():reject(x.status);
    x.onerror=()=>reject(0);
    x.send(file);
  });
}
async function sendFiles(files){
  if(!API){toast(T("filesNeedServer"));return}
  if(uploading){toast(T("waitUpload"));return}
  files=[...files];
  const big=files.find(f=>f.size>MAX_FILE);
  if(big){toast(T("over100",big.name));return}
  if(!files.length)return;
  uploading=true;$("#pickFile").disabled=true;$("#prog").hidden=false;
  try{
    for(let k=0;k<files.length;k++){
      $("#fileHint").textContent=T("sending",k+1,files.length,files[k].name);
      $("#prog i").style.width="0";
      await uploadOne(files[k]);
    }
    toast(files.length>1?T("filesSent"):T("fileSent"));poll();
  }catch(e){toast(e===413?T("fileTooBig"):T("fileFail"))}
  finally{uploading=false;$("#pickFile").disabled=false;$("#prog").hidden=true;$("#fileHint").textContent=T("fileHint")}
}
$("#pickFile").onclick=()=>$("#fileInput").click();
$("#fileInput").onchange=e=>{sendFiles(e.target.files);e.target.value=""};
const comp=document.querySelector(".composer");
document.addEventListener("dragover",e=>{if(e.dataTransfer&&[...e.dataTransfer.types].includes("Files")){e.preventDefault();comp.classList.add("drop")}});
document.addEventListener("dragleave",e=>{if(!e.relatedTarget)comp.classList.remove("drop")});
document.addEventListener("drop",e=>{if(e.dataTransfer&&e.dataTransfer.files.length){e.preventDefault();comp.classList.remove("drop");sendFiles(e.dataTransfer.files)}});

function goLocal(msg){
  localMode=true;col=null;setStatus(false,T("localOnly"));
  const n=$("#notice");n.textContent=msg;n.hidden=false;loadLocal();render();
}

sendBtn.onclick=send;
$("#paste").onclick=async()=>{
  let text=null;
  if(APP){ try{ text=APP.paste(); }catch(e){} }
  if(text===null) try{ if(navigator.clipboard&&navigator.clipboard.readText) text=await navigator.clipboard.readText(); }catch(e){}
  if(text===null){ input.focus(); toast(DEVICE==="phone"?T("holdPaste"):T("pressCtrlV")); return; }
  if(!text){ toast(T("clipEmpty")); return; }
  const s=input.selectionStart, e=input.selectionEnd;
  input.setRangeText(text,s,e,"end"); input.focus(); input.dispatchEvent(new Event("input"));
  toast(T("pasted"));
};
$("#clear").onclick=()=>{input.value="";input.focus();try{localStorage.removeItem("jumper-draft")}catch(e){}};
input.addEventListener("keydown",e=>{
  if(e.key==="Enter"&&(e.ctrlKey||e.metaKey)){e.preventDefault();send()}
  if(e.key==="Tab"&&!e.shiftKey){e.preventDefault();const s=input.selectionStart;input.setRangeText("  ",s,input.selectionEnd,"end")}
});
let draftT;input.addEventListener("input",()=>{clearTimeout(draftT);draftT=setTimeout(()=>{try{localStorage.setItem("jumper-draft",input.value)}catch(e){}},400)});
try{input.value=localStorage.getItem("jumper-draft")||""}catch(e){}

render();

// Синхронизация через jumper.py (локальная сеть). Если файл открыт напрямую — работает только на этом устройстве.
const API = location.protocol.startsWith("http") ? "/api/clips" : null;
col = API ? {
  add: c => fetch(API,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(c)}).then(r=>{if(!r.ok)throw 0;poll()}),
  doc: id => ({delete: () => fetch(API+"/"+encodeURIComponent(id),{method:"DELETE"}).then(r=>{if(!r.ok)throw 0;poll()})})
} : null;
let lastJson="", online=null;
async function poll(){
  try{
    const r=await fetch(API,{cache:"no-store"}); if(!r.ok) throw 0;
    const txt=await r.text();
    if(online!==true){online=true;setStatus(true,T("synced")+" · "+devName(DEVICE));$("#notice").hidden=true}
    if(txt===lastJson) return;
    const first=lastJson==="", prevTop=clips[0]&&clips[0].id;
    lastJson=txt; clips=JSON.parse(txt);
    const top=clips[0];
    if(!first&&top&&top.id!==prevTop){lastSeen=top.id;if(devCode(top.device)!==DEVICE)toast(T("newFrom",devName(top.device)))}
    render();
  }catch(e){
    if(online!==false){online=false;setStatus(false,T("noPc"));const n=$("#notice");n.textContent=T("noServer");n.hidden=false}
  }
}
if(API){ poll(); setInterval(()=>{ if(!document.hidden) poll(); },1500); document.addEventListener("visibilitychange",()=>{if(!document.hidden)poll()}); }
else goLocal(T("direct"));
</script>
</body></html>
'''


if __name__ == "__main__":
    ip = lan_ip()
    print(msg("started"))
    print(msg("this_pc") + f"http://localhost:{PORT}")
    print(msg("phone") + f"http://{ip}:{PORT}")
    print(msg("hint"))
    try:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    except OSError:
        print(msg("busy") % PORT)
        webbrowser.open(f"http://localhost:{PORT}")
        input(msg("enter"))
        sys.exit(1)
    threading.Thread(target=discovery_responder, daemon=True).start()
    webbrowser.open(f"http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
