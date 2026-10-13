# -*- coding: utf-8 -*-
"""Grok 工作台：本机中文图形界面，调用 Grok 命令行完成全部工具能力。"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def static_root() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS")) / "static"
    return install_root() / "static"


def find_edge() -> Path | None:
    bases = [
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    for base in bases:
        if not base:
            continue
        candidate = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if candidate.is_file():
            return candidate
    return None


ROOT = install_root()
STATIC = static_root()
DATA = ROOT / "data"
SESS_DIR = DATA / "sessions"
RUNS = DATA / "runs"
UPLOADS = DATA / "uploads"
PORT_FILE = DATA / "port.json"
CONFIG_FILE = DATA / "config.json"
INDEX_FILE = DATA / "index.json"
LOG_FILE = DATA / "server.log"

GROK_DEFAULT = Path.home() / ".grok" / "bin" / "grok.exe"
CREATE_NO_WINDOW = 0x08000000

MODELS = [
    {"id": "grok-4.7", "label": "Grok 4.7"},
    {"id": "grok-4.7-build-fast", "label": "Grok 4.7 快速"},
    {"id": "grok-4.6", "label": "Grok 4.6"},
    {"id": "grok-4.5", "label": "Grok 4.5"},
]
EFFORTS = [
    {"id": "low", "label": "快速"},
    {"id": "medium", "label": "标准"},
    {"id": "high", "label": "深入"},
    {"id": "xhigh", "label": "更深入"},
]

READ_DENY = ",".join([
    "bash",
    "run_terminal_cmd",
    "run_terminal_command",
    "search_replace",
    "write",
    "edit",
    "task",
    "spawn_subagent",
    "kill_task",
    "kill_command_or_subagent",
    "get_task_output",
    "get_command_or_subagent_output",
])

SECRET_NAMES = {
    "auth.json",
    ".env",
    ".netrc",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "credentials.json",
    "cookies.txt",
}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico"}
SAFE_IMAGE_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
}
MAX_UPLOAD = 20 * 1024 * 1024
MAX_UPLOAD_BODY = 28 * 1024 * 1024
MAX_ATTACHMENTS = 8
ID_RE = re.compile(r"[0-9a-f]{32}")
TEXT_EXT = {
    ".txt", ".md", ".markdown", ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx",
    ".json", ".css", ".html", ".htm", ".xml", ".yml", ".yaml", ".toml",
    ".ini", ".cfg", ".ps1", ".bat", ".cmd", ".rs", ".go", ".java", ".c",
    ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".sh", ".sql", ".log",
    ".csv", ".svg", ".vue", ".svelte", ".lua", ".r", ".m", ".gradle",
    ".properties", ".env.example", ".gitignore", ".gitattributes",
}

RULES = (
    "你正在「Grok 工作台」图形界面里回答用户。"
    "使用简体中文，直接完成请求。"
    "需要时使用工具：阅读和修改文件、运行命令、搜索代码、检索网页、拆分子任务。"
    "用户消息如果列出了附件路径，必须先用 read_file 打开每一个附件再回答。"
    "图片会作为图像返回，按画面内容回答，不要说自己看不到图。"
    "做完用简短的话说明结果。"
)

LOCK = threading.Lock()
SESSIONS: dict[str, dict] = {}
ORDER: list[str] = []
TURNS: dict[str, "Turn"] = {}
CONFIG: dict = {}


def log(message: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + message
    try:
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def ensure_dirs() -> None:
    for path in (DATA, SESS_DIR, RUNS, UPLOADS, STATIC):
        path.mkdir(parents=True, exist_ok=True)


def default_config() -> dict:
    return {
        "workspace": r"D:\AI\GrokBuild",
        "model": "grok-4.7",
        "effort": "high",
        "permission": "full",
        "grokPath": str(GROK_DEFAULT),
    }


def load_config() -> None:
    global CONFIG
    CONFIG = default_config()
    if CONFIG_FILE.exists():
        try:
            stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                CONFIG.update({k: stored[k] for k in CONFIG if k in stored})
        except (OSError, json.JSONDecodeError):
            log("配置读取失败，已使用默认配置")
    normalize_config()


def normalize_config() -> None:
    if CONFIG.get("effort") not in {item["id"] for item in EFFORTS}:
        CONFIG["effort"] = "high"
    if CONFIG.get("permission") not in {"full", "read"}:
        CONFIG["permission"] = "full"
    if not str(CONFIG.get("model") or "").strip():
        CONFIG["model"] = "grok-4.7"
    workspace = Path(str(CONFIG.get("workspace") or ROOT))
    if not workspace.is_dir():
        CONFIG["workspace"] = str(ROOT)
    grok_path = Path(str(CONFIG.get("grokPath") or GROK_DEFAULT))
    if not grok_path.is_file():
        CONFIG["grokPath"] = str(GROK_DEFAULT)


def save_config() -> None:
    normalize_config()
    CONFIG_FILE.write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")


def load_sessions() -> None:
    global ORDER
    SESSIONS.clear()
    ORDER = []
    if INDEX_FILE.exists():
        try:
            ORDER = list(json.loads(INDEX_FILE.read_text(encoding="utf-8")).get("order", []))
        except (OSError, json.JSONDecodeError):
            ORDER = []
    kept = []
    for sid in ORDER:
        path = SESS_DIR / f"{sid}.json"
        if not path.exists():
            continue
        try:
            session = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        session["running"] = False
        SESSIONS[sid] = session
        kept.append(sid)
    ORDER = kept


def persist_index() -> None:
    INDEX_FILE.write_text(json.dumps({"order": ORDER}, ensure_ascii=False, indent=2), encoding="utf-8")


def persist_session(session: dict) -> None:
    path = SESS_DIR / f"{session['id']}.json"
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_id(value: str) -> str:
    text = str(value or "")
    if not ID_RE.fullmatch(text):
        raise ValueError("编号无效")
    return text


def safe_filename(name: str) -> str:
    base = Path(str(name or "")).name.strip()
    cleaned = "".join("_" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch for ch in base)
    cleaned = cleaned.strip().strip(".")
    return (cleaned or "附件")[:80]


def attachment_kind(name: str, mime: str) -> str:
    ext = Path(name).suffix.lower()
    if mime in SAFE_IMAGE_MIME or ext in IMAGE_EXT:
        return "image"
    return "file"


def public_attachment(item: dict) -> dict:
    return {key: item.get(key) for key in ("id", "name", "mime", "kind", "bytes")}


def public_messages(messages: list) -> list:
    cleaned = []
    for message in messages:
        if not isinstance(message, dict):
            cleaned.append(message)
            continue
        item = dict(message)
        attachments = item.get("attachments")
        if isinstance(attachments, list):
            item["attachments"] = [
                public_attachment(att) for att in attachments if isinstance(att, dict)
            ]
        cleaned.append(item)
    return cleaned


def compose_prompt(text: str, attachments: list[dict]) -> str:
    visible = text.strip()
    if not attachments:
        return visible
    lines = [
        "用户通过工作台附上了文件。请先用 read_file 打开下面每一个路径，看完再回答。",
        "图片会作为图像返回，请按画面内容回答，不要说自己看不到图。",
    ]
    for index, item in enumerate(attachments, 1):
        label = "图片" if item.get("kind") == "image" else "文件"
        lines.append(f"{index}. {label} {item.get('name') or '附件'}：{item['path']}")
    lines.append("")
    lines.append(visible or "请看这些附件。")
    return "\n".join(lines)


def save_upload(session_id: str, name: str, mime: str, raw: bytes) -> dict:
    sid = safe_id(session_id)
    if sid not in SESSIONS:
        raise RuntimeError("对话不存在")
    if not raw:
        raise RuntimeError("文件是空的")
    if len(raw) > MAX_UPLOAD:
        raise RuntimeError("文件超过 20 MB")
    filename = safe_filename(name)
    ext = Path(filename).suffix.lower()
    mime_key = (mime or "").split(";", 1)[0].strip().lower()
    if mime_key == "image/jpg":
        mime_key = "image/jpeg"
    if ext not in IMAGE_EXT and not re.fullmatch(r"\.[a-z0-9]{1,8}", ext):
        ext = SAFE_IMAGE_MIME.get(mime_key, "")
        if ext and not filename.lower().endswith(ext):
            filename += ext
    kind = attachment_kind(filename, mime_key)
    if kind == "image" and ext not in IMAGE_EXT:
        ext = SAFE_IMAGE_MIME.get(mime_key, ".png")
        filename = Path(filename).stem + ext
    if kind == "image" and mime_key not in SAFE_IMAGE_MIME:
        mime_key = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
            ".ico": "image/x-icon",
        }.get(ext, "image/png")
    folder = UPLOADS / sid
    folder.mkdir(parents=True, exist_ok=True)
    fid = uuid.uuid4().hex
    path = folder / f"{fid}{ext}"
    path.write_bytes(raw)
    meta = {
        "id": fid,
        "sessionId": sid,
        "name": filename,
        "mime": mime_key or "application/octet-stream",
        "kind": kind,
        "bytes": len(raw),
        "path": str(path),
    }
    (folder / f"{fid}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return public_attachment(meta) | {"sessionId": sid}


def load_attachment(session_id: str, file_id: str) -> dict:
    sid = safe_id(session_id)
    fid = safe_id(file_id)
    meta_path = UPLOADS / sid / f"{fid}.json"
    if not meta_path.is_file():
        raise RuntimeError("附件不存在或已失效")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if not isinstance(meta, dict) or "path" not in meta:
        raise RuntimeError("附件已损坏")
    path = Path(str(meta["path"])).resolve()
    folder = (UPLOADS / sid).resolve()
    if path.parent != folder or not path.is_file():
        raise RuntimeError("附件路径无效")
    meta["path"] = str(path)
    return meta


def delete_uploads(session_id: str) -> None:
    try:
        sid = safe_id(session_id)
    except ValueError:
        return
    folder = UPLOADS / sid
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)


def public_session(session: dict, full: bool = False) -> dict:
    item = {
        "id": session["id"],
        "title": session.get("title") or "新对话",
        "cwd": session.get("cwd") or CONFIG["workspace"],
        "updated": session.get("updated"),
        "created": session.get("created"),
        "running": bool(session.get("running")),
        "model": session.get("model") or CONFIG["model"],
    }
    if full:
        item["messages"] = public_messages(session.get("messages") or [])
        item["grokSessionId"] = session.get("grokSessionId")
    return item


def create_session(cwd: str | None = None) -> dict:
    folder = Path(cwd or CONFIG["workspace"])
    if not folder.is_dir():
        folder = Path(CONFIG["workspace"])
    session = {
        "id": uuid.uuid4().hex,
        "title": "新对话",
        "cwd": str(folder),
        "grokSessionId": None,
        "model": CONFIG["model"],
        "created": now_iso(),
        "updated": now_iso(),
        "running": False,
        "messages": [],
    }
    with LOCK:
        SESSIONS[session["id"]] = session
        ORDER.insert(0, session["id"])
        persist_session(session)
        persist_index()
    return session


def is_secret(path: Path) -> bool:
    name = path.name.lower()
    if name in SECRET_NAMES or name.startswith(".env"):
        return True
    if path.suffix.lower() in {".pem", ".key", ".pfx", ".p12"}:
        return True
    return name.startswith("id_rsa")


def resolve_under(cwd: Path, rel: str) -> Path:
    root = cwd.resolve()
    if not root.is_dir():
        raise ValueError("工作目录不存在")
    raw = (rel or "").strip().replace("/", "\\")
    if raw.startswith("\\\\") or raw.startswith("//"):
        raise ValueError("不能使用网络路径")
    candidate = Path(raw)
    if candidate.is_absolute() or candidate.drive:
        raise ValueError("请使用工作目录里的相对路径")
    target = (root / candidate).resolve() if raw else root
    root_key = os.path.normcase(str(root))
    target_key = os.path.normcase(str(target))
    if os.path.commonpath([root_key, target_key]) != root_key:
        raise ValueError("路径超出工作目录")
    return target


def summarize_input(raw) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()[:240]
    if isinstance(raw, dict):
        for key in ("command", "cmd", "path", "file_path", "target_file", "file", "query", "pattern", "url", "prompt"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:240]
        try:
            text = json.dumps(raw, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(raw)
        return text[:240]
    return str(raw)[:240]


def summarize_output(raw) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        text = raw
    else:
        try:
            text = json.dumps(raw, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(raw)
    text = text.strip()
    if len(text) > 360:
        return text[:360] + "…"
    return text


class Turn:
    def __init__(self) -> None:
        self.queue: queue.Queue = queue.Queue()
        self.proc: subprocess.Popen | None = None
        self.stopped = False
        self.got_end = False
        self.grok_id = None
        self.usage = None
        self.chunks = 0
        self.assistant = {
            "role": "assistant",
            "text": "",
            "thought": "",
            "tools": [],
            "plan": [],
            "usage": None,
            "at": now_iso(),
        }


def kill_proc(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except OSError:
            pass


def apply_event(session_id: str, turn: Turn, event: dict) -> None:
    kind = event.get("type")
    assistant = turn.assistant
    if kind == "text":
        assistant["text"] += event.get("data") or event.get("text") or ""
    elif kind == "thought":
        assistant["thought"] += event.get("data") or ""
        if len(assistant["thought"]) > 20000:
            assistant["thought"] = assistant["thought"][-20000:]
    elif kind == "tool_call":
        assistant["tools"].append({
            "id": event.get("toolCallId") or uuid.uuid4().hex,
            "name": event.get("toolName") or event.get("title") or "工具",
            "title": event.get("title") or "",
            "status": event.get("status") or "in_progress",
            "input": summarize_input(event.get("rawInput")),
            "output": "",
        })
    elif kind == "tool_call_update":
        tool_id = event.get("toolCallId")
        for tool in assistant["tools"]:
            if tool["id"] == tool_id:
                if event.get("status"):
                    tool["status"] = event["status"]
                output = summarize_output(event.get("rawOutput"))
                if output:
                    tool["output"] = output
                break
    elif kind == "plan":
        entries = event.get("entries") or event.get("data") or []
        if isinstance(entries, list):
            assistant["plan"] = entries[:40]
    elif kind == "end":
        turn.got_end = True
        turn.grok_id = event.get("sessionId") or event.get("session_id") or turn.grok_id
        turn.usage = event.get("usage") or turn.usage
        if not assistant["text"]:
            assistant["text"] = event.get("text") or ""
    elif kind == "error":
        message = event.get("message") or "Grok 返回了错误"
        if message not in assistant["text"]:
            assistant["text"] += (("\n\n" if assistant["text"] else "") + message)

    turn.chunks += 1
    should_save = kind in {"tool_call", "tool_call_update", "plan", "end", "error"} or turn.chunks % 12 == 0
    if should_save:
        remember(session_id, turn, running=True)


def assistant_snapshot(turn: Turn, running: bool) -> dict:
    usage = None
    if isinstance(turn.usage, dict):
        usage = {
            "input_tokens": turn.usage.get("input_tokens"),
            "output_tokens": turn.usage.get("output_tokens"),
        }
    snapshot = {**turn.assistant, "live": running}
    if usage:
        snapshot["usage"] = usage
    return snapshot


def remember(session_id: str, turn: Turn, running: bool) -> None:
    with LOCK:
        session = SESSIONS.get(session_id)
        if not session:
            return
        messages = session.setdefault("messages", [])
        snapshot = assistant_snapshot(turn, running)
        if messages and messages[-1].get("role") == "assistant":
            messages[-1] = snapshot
        else:
            messages.append(snapshot)
        if turn.grok_id:
            session["grokSessionId"] = turn.grok_id
        if snapshot.get("usage"):
            session["usage"] = snapshot["usage"]
        session["running"] = running
        session["updated"] = now_iso()
        persist_session(session)


def finish_turn(session_id: str, turn: Turn) -> None:
    with LOCK:
        session = SESSIONS.get(session_id)
        if not session:
            return
        messages = session.setdefault("messages", [])
        final = assistant_snapshot(turn, running=False)
        if messages and messages[-1].get("role") == "assistant":
            messages[-1] = final
        else:
            messages.append(final)
        if turn.grok_id:
            session["grokSessionId"] = turn.grok_id
        session["running"] = False
        session["updated"] = now_iso()
        persist_session(session)
        if session_id in ORDER:
            ORDER.remove(session_id)
        ORDER.insert(0, session_id)
        persist_index()
    TURNS.pop(session_id, None)


def grok_env() -> dict:
    env = os.environ.copy()
    env["GROK_DISABLE_AUTOUPDATER"] = "1"
    env["GROK_WEB_FETCH"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def build_command(session: dict, prompt_path: Path) -> list[str]:
    grok = str(CONFIG.get("grokPath") or GROK_DEFAULT)
    command = [
        grok,
        "--prompt-file", str(prompt_path),
        "--verbatim",
        "--output-format", "streaming-json",
        "--cwd", session["cwd"],
        "--no-auto-update",
        "-m", CONFIG["model"],
        "--effort", CONFIG["effort"],
        "--rules", RULES if CONFIG["permission"] == "full" else RULES + "当前是只读模式：不要修改文件，不要运行命令。",
    ]
    if session.get("grokSessionId"):
        command += ["--resume", session["grokSessionId"]]
    if CONFIG["permission"] == "full":
        command.append("--yolo")
    else:
        command += ["--disallowed-tools", READ_DENY, "--no-subagents"]
    return command


def reader_thread(session_id: str, turn: Turn, prompt_path: Path, err_path: Path) -> None:
    proc = turn.proc
    assert proc is not None
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip().lstrip("\ufeff")
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            apply_event(session_id, turn, event)
            simple = simplify_event(event)
            if simple:
                turn.queue.put(simple)
        code = proc.wait()
        error_text = ""
        try:
            error_text = err_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            error_text = ""
        if turn.stopped:
            if not turn.assistant["text"]:
                turn.assistant["text"] = "已停止。"
            turn.queue.put({"type": "error", "message": "已停止"})
        elif not turn.got_end and code != 0:
            message = error_text[-1500:] if error_text else f"Grok 已退出（代码 {code}）"
            turn.assistant["text"] += (("\n\n" if turn.assistant["text"] else "") + message)
            turn.queue.put({"type": "error", "message": message})
        usage = None
        if isinstance(turn.usage, dict):
            usage = {
                "input_tokens": turn.usage.get("input_tokens"),
                "output_tokens": turn.usage.get("output_tokens"),
            }
        turn.queue.put({"type": "done", "sessionId": turn.grok_id, "usage": usage})
    except Exception as exc:
        log(f"读取 Grok 输出失败: {exc}")
        turn.queue.put({"type": "error", "message": f"读取输出失败：{exc}"})
        turn.queue.put({"type": "done", "sessionId": turn.grok_id, "usage": None})
    finally:
        finish_turn(session_id, turn)
        turn.queue.put(None)
        for path in (prompt_path, err_path):
            try:
                path.unlink()
            except OSError:
                pass


def simplify_event(event: dict):
    kind = event.get("type")
    if kind == "text":
        return {"type": "text", "data": event.get("data") or event.get("text") or ""}
    if kind == "thought":
        return {"type": "thought", "data": event.get("data") or ""}
    if kind == "tool_call":
        return {
            "type": "tool",
            "id": event.get("toolCallId"),
            "name": event.get("toolName") or event.get("title") or "工具",
            "title": event.get("title") or "",
            "status": event.get("status") or "in_progress",
            "input": summarize_input(event.get("rawInput")),
        }
    if kind == "tool_call_update":
        return {
            "type": "tool_update",
            "id": event.get("toolCallId"),
            "status": event.get("status") or "",
            "output": summarize_output(event.get("rawOutput")),
        }
    if kind == "plan":
        return {"type": "plan", "entries": event.get("entries") or []}
    if kind == "error":
        return {"type": "error", "message": event.get("message") or "出错了"}
    if kind == "end":
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else None
        compact = None
        if usage:
            compact = {
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
            }
        return {"type": "meta", "sessionId": event.get("sessionId"), "usage": compact}
    return None


def start_turn(session_id: str, text: str, attachments: list[dict] | None = None) -> tuple[Turn, dict]:
    attachments = list(attachments or [])
    grok = Path(str(CONFIG.get("grokPath") or GROK_DEFAULT))
    if not grok.is_file():
        raise RuntimeError("找不到 Grok。请在设置里填写 grok.exe 的路径。")
    with LOCK:
        session = SESSIONS.get(session_id)
        if session is None:
            raise RuntimeError("对话不存在")
        if session.get("running") or session_id in TURNS:
            raise RuntimeError("这个对话还在进行")
        folder = Path(session.get("cwd") or CONFIG["workspace"])
        if not folder.is_dir():
            raise RuntimeError("工作目录不存在，请先更换目录")
        visible = text.strip()
        title_line = visible.splitlines()[0].strip() if visible else ""
        if not title_line and attachments:
            title_line = str(attachments[0].get("name") or "附件")
        if session.get("title") in {None, "", "新对话"}:
            session["title"] = (title_line or "新对话")[:24]
        session["model"] = CONFIG["model"]
        user_message = {"role": "user", "text": visible, "at": now_iso()}
        if attachments:
            user_message["attachments"] = [public_attachment(item) for item in attachments]
        session["messages"].append(user_message)
        prompt = compose_prompt(visible, attachments)
        session["running"] = True
        session["updated"] = now_iso()
        if session_id in ORDER:
            ORDER.remove(session_id)
        ORDER.insert(0, session_id)
        persist_session(session)
        persist_index()
        snapshot = json.loads(json.dumps(session, ensure_ascii=False))

    turn = Turn()
    RUNS.mkdir(parents=True, exist_ok=True)
    prompt_path = RUNS / f"{session_id}-{uuid.uuid4().hex}.txt"
    err_path = RUNS / f"{session_id}-{uuid.uuid4().hex}.err"
    prompt_path.write_text(prompt, encoding="utf-8")
    command = build_command(snapshot, prompt_path)
    log(f"开始对话 {session_id} model={CONFIG['model']} permission={CONFIG['permission']}")
    err_handle = err_path.open("w", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            command,
            cwd=snapshot["cwd"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=err_handle,
            env=grok_env(),
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError as exc:
        err_handle.close()
        with LOCK:
            session = SESSIONS[session_id]
            session["running"] = False
            session["messages"].append({
                "role": "assistant",
                "text": f"无法启动 Grok：{exc}",
                "thought": "",
                "tools": [],
                "plan": [],
                "at": now_iso(),
            })
            persist_session(session)
        raise RuntimeError(f"无法启动 Grok：{exc}") from exc
    turn.proc = proc
    err_handle.close()
    TURNS[session_id] = turn
    remember(session_id, turn, running=True)
    threading.Thread(
        target=reader_thread,
        args=(session_id, turn, prompt_path, err_path),
        daemon=True,
    ).start()
    return turn, snapshot


def list_directory(cwd: Path, rel: str) -> dict:
    target = resolve_under(cwd, rel)
    if not target.exists():
        raise FileNotFoundError("找不到这个位置")
    if not target.is_dir():
        raise NotADirectoryError("这不是文件夹")
    entries = []
    truncated = False
    try:
        children = list(target.iterdir())
    except OSError as exc:
        raise RuntimeError(f"无法读取文件夹：{exc}") from exc
    children.sort(key=lambda item: (not item.is_dir(), item.name.lower()))
    if len(children) > 400:
        children = children[:400]
        truncated = True
    for child in children:
        try:
            is_dir = child.is_dir()
            size = 0 if is_dir else child.stat().st_size
        except OSError:
            continue
        entries.append({
            "name": child.name,
            "dir": is_dir,
            "size": size,
            "secret": is_secret(child),
        })
    root = cwd.resolve()
    try:
        shown = str(target.resolve().relative_to(root))
    except ValueError:
        shown = ""
    if shown == ".":
        shown = ""
    parent = None
    if target.resolve() != root:
        parent_path = target.resolve().parent
        try:
            parent = str(parent_path.relative_to(root))
        except ValueError:
            parent = ""
        if parent == ".":
            parent = ""
    return {"rel": shown, "parent": parent, "entries": entries, "truncated": truncated}


def read_preview(cwd: Path, rel: str) -> dict:
    target = resolve_under(cwd, rel)
    if not target.is_file():
        raise FileNotFoundError("找不到这个文件")
    if is_secret(target):
        return {"name": target.name, "blocked": True, "text": ""}
    size = target.stat().st_size
    suffix = target.suffix.lower()
    if suffix in IMAGE_EXT:
        if size > 15_000_000:
            return {"name": target.name, "binary": True, "text": "图片太大，无法预览"}
        return {"name": target.name, "image": True, "text": ""}
    if suffix and suffix not in TEXT_EXT and size > 1_000_000:
        return {"name": target.name, "binary": True, "text": ""}
    data = target.read_bytes()[:200_001]
    if b"\x00" in data[:1024]:
        return {"name": target.name, "binary": True, "text": ""}
    truncated = size > 200_000
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("gbk", errors="replace")
    if truncated:
        text = text[:200_000]
    return {"name": target.name, "text": text, "truncated": truncated}


def run_terminal(cwd: Path, command: str) -> dict:
    command = command.strip()
    if not command:
        raise RuntimeError("请输入命令")
    if len(command) > 8000:
        raise RuntimeError("命令太长")
    if not cwd.is_dir():
        raise RuntimeError("工作目录不存在")
    RUNS.mkdir(parents=True, exist_ok=True)
    script = RUNS / f"term-{uuid.uuid4().hex}.ps1"
    body = (
        "$OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
        + command
        + "\n"
    )
    script.write_text(body, encoding="utf-8-sig")
    proc = subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW,
    )
    timed_out = False
    try:
        output, _ = proc.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_proc(proc)
        output, _ = proc.communicate()
    finally:
        try:
            script.unlink()
        except OSError:
            pass
    text = output.decode("utf-8", errors="replace")
    if text.count("\ufffd") > 8:
        text = output.decode("gbk", errors="replace")
    if len(text) > 100_000:
        text = text[-100_000:]
    if timed_out:
        text += "\n\n命令超时，已停止。"
    return {"code": proc.returncode, "output": text, "timedOut": timed_out}


def pick_folder(initial: str | None = None) -> str | None:
    script = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $true
$owner.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$owner.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedToolWindow
$owner.Text = "选择目录"
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.Opacity = 0
$owner.Show()
$owner.Activate()
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = "选择工作目录"
$dialog.ShowNewFolderButton = $true
$initial = $env:GROK_PICK_INITIAL
if ($initial -and (Test-Path -LiteralPath $initial)) {
  $dialog.SelectedPath = $initial
}
$result = $dialog.ShowDialog($owner)
$owner.Close()
$owner.Dispose()
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
  [Console]::Out.WriteLine($dialog.SelectedPath)
}
"""
    env = os.environ.copy()
    env["GROK_PICK_INITIAL"] = str(initial or CONFIG.get("workspace") or "")
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=300,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"无法打开文件夹选择窗口：{exc}") from exc
    text = completed.stdout.decode("utf-8", errors="replace").strip()
    if not text:
        text = completed.stdout.decode("gbk", errors="replace").strip()
    if completed.returncode not in (0, None) and not text:
        err = completed.stderr.decode("utf-8", errors="replace").strip()
        if not err:
            err = completed.stderr.decode("gbk", errors="replace").strip()
        log(f"选择目录失败 code={completed.returncode} {err[-500:]}")
        raise RuntimeError(err[-300:] or "无法打开文件夹选择窗口")
    return text or None


def decode_body(handler: BaseHTTPRequestHandler, limit: int = 2_000_000) -> dict:
    length = int(handler.headers.get("Content-Length") or "0")
    if length > limit:
        raise ValueError("内容太大")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("需要 JSON 对象")
    return data


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "GrokDesktop/1.0"

    def log_message(self, fmt: str, *args) -> None:
        log(fmt % args)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def reject_foreign(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in {"127.0.0.1", "localhost"}:
            self.send_error(403)
            return True
        origin = self.headers.get("Origin")
        if origin:
            port = self.server.server_address[1]
            allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
            if origin not in allowed:
                self.send_error(403)
                return True
        return False

    def send_json(self, payload, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.reject_foreign():
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if path in {"/", "/index.html"}:
                self.serve_static("index.html")
                return
            if path.startswith("/static/"):
                self.serve_static(path[len("/static/"):])
                return
            if path == "/api/health":
                self.send_json({"ok": True, "app": "grok-desktop"})
                return
            if path == "/api/bootstrap":
                self.send_json(self.bootstrap())
                return
            if path.startswith("/api/sessions/"):
                sid = path.split("/")[-1]
                with LOCK:
                    session = SESSIONS.get(sid)
                    payload = public_session(session, full=True) if session else None
                if payload is None:
                    self.send_json({"error": "对话不存在"}, 404)
                    return
                self.send_json(payload)
                return
            if path == "/api/files":
                cwd = self.cwd_from_query(query)
                rel = query.get("path", [""])[0]
                self.send_json(list_directory(cwd, rel))
                return
            if path == "/api/file":
                cwd = self.cwd_from_query(query)
                rel = query.get("path", [""])[0]
                self.send_json(read_preview(cwd, rel))
                return
            if path == "/api/raw":
                self.serve_image(query)
                return
            if path.startswith("/api/attachment/"):
                parts = [part for part in path.split("/") if part]
                if len(parts) != 4:
                    self.send_error(404)
                    return
                self.serve_attachment(parts[2], parts[3])
                return
            self.send_error(404)
        except FileNotFoundError as exc:
            self.send_json({"error": str(exc)}, 404)
        except (ValueError, NotADirectoryError, RuntimeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            log(f"GET {path} 失败: {exc}")
            self.send_json({"error": "请求失败"}, 500)

    def do_POST(self) -> None:
        if self.reject_foreign():
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        limit = MAX_UPLOAD_BODY if path == "/api/upload" else 2_000_000
        try:
            data = decode_body(self, limit)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
            return
        try:
            if path == "/api/sessions":
                session = create_session(data.get("cwd"))
                self.send_json(public_session(session, full=True))
                return
            if path == "/api/upload":
                self.handle_upload(data)
                return
            if path == "/api/chat":
                self.handle_chat(data)
                return
            if path == "/api/stop":
                sid = str(data.get("sessionId") or "")
                turn = TURNS.get(sid)
                if turn:
                    turn.stopped = True
                    kill_proc(turn.proc)
                self.send_json({"ok": True})
                return
            if path == "/api/config":
                self.handle_config(data)
                return
            if path == "/api/pick-folder":
                chosen = pick_folder(str(data.get("initial") or "") or None)
                self.send_json({"path": chosen})
                return
            if path == "/api/terminal":
                cwd = Path(str(data.get("cwd") or CONFIG["workspace"]))
                result = run_terminal(cwd, str(data.get("command") or ""))
                self.send_json(result)
                return
            if path == "/api/shutdown":
                self.send_json({"ok": True})
                threading.Thread(target=shutdown, daemon=True).start()
                return
            self.send_error(404)
        except RuntimeError as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            log(f"POST {path} 失败: {exc}")
            self.send_json({"error": "请求失败"}, 500)

    def do_PATCH(self) -> None:
        if self.reject_foreign():
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if not path.startswith("/api/sessions/"):
            self.send_error(404)
            return
        sid = path.split("/")[-1]
        try:
            data = decode_body(self)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
            return
        with LOCK:
            session = SESSIONS.get(sid)
            if session is None:
                self.send_json({"error": "对话不存在"}, 404)
                return
            if "title" in data:
                title = str(data.get("title") or "").strip()[:40]
                if title:
                    session["title"] = title
            if "cwd" in data:
                folder = Path(str(data.get("cwd") or ""))
                if not folder.is_dir():
                    self.send_json({"error": "文件夹不存在"}, 400)
                    return
                session["cwd"] = str(folder)
                session["grokSessionId"] = None
            session["updated"] = now_iso()
            persist_session(session)
            payload = public_session(session, full=True)
        self.send_json(payload)

    def do_DELETE(self) -> None:
        if self.reject_foreign():
            return
        path = unquote(urlparse(self.path).path)
        if not path.startswith("/api/sessions/"):
            self.send_error(404)
            return
        sid = path.split("/")[-1]
        turn = TURNS.get(sid)
        if turn:
            turn.stopped = True
            kill_proc(turn.proc)
        with LOCK:
            SESSIONS.pop(sid, None)
            if sid in ORDER:
                ORDER.remove(sid)
            persist_index()
        file_path = SESS_DIR / f"{sid}.json"
        try:
            file_path.unlink()
        except OSError:
            pass
        delete_uploads(sid)
        self.send_json({"ok": True})

    def bootstrap(self) -> dict:
        grok = Path(str(CONFIG.get("grokPath") or GROK_DEFAULT))
        with LOCK:
            sessions = [public_session(SESSIONS[sid]) for sid in ORDER if sid in SESSIONS]
        return {
            "config": CONFIG,
            "models": MODELS,
            "efforts": EFFORTS,
            "sessions": sessions,
            "grok": {"path": str(grok), "ok": grok.is_file()},
        }

    def cwd_from_query(self, query: dict) -> Path:
        raw = query.get("cwd", [CONFIG["workspace"]])[0]
        folder = Path(raw)
        if not folder.is_dir():
            raise ValueError("工作目录不存在")
        return folder

    def serve_static(self, name: str) -> None:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            self.send_error(404)
            return
        file_path = (STATIC / relative).resolve()
        if file_path.parent != STATIC.resolve() and STATIC.resolve() not in file_path.parents:
            self.send_error(404)
            return
        if not file_path.is_file():
            self.send_error(404)
            return
        mime = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
        }.get(file_path.suffix.lower(), "application/octet-stream")
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def serve_image(self, query: dict) -> None:
        cwd = self.cwd_from_query(query)
        rel = query.get("path", [""])[0]
        target = resolve_under(cwd, rel)
        if not target.is_file() or target.suffix.lower() not in IMAGE_EXT or is_secret(target):
            self.send_error(404)
            return
        if target.stat().st_size > 15_000_000:
            self.send_error(413)
            return
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
            ".ico": "image/x-icon",
        }.get(target.suffix.lower(), "application/octet-stream")
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_config(self, data: dict) -> None:
        if "model" in data and str(data["model"]).strip():
            CONFIG["model"] = str(data["model"]).strip()
        if "effort" in data:
            CONFIG["effort"] = str(data["effort"])
        if "permission" in data:
            CONFIG["permission"] = str(data["permission"])
        if "workspace" in data:
            folder = Path(str(data["workspace"]))
            if not folder.is_dir():
                raise RuntimeError("文件夹不存在")
            CONFIG["workspace"] = str(folder)
        if "grokPath" in data and str(data["grokPath"]).strip():
            CONFIG["grokPath"] = str(data["grokPath"]).strip()
        with LOCK:
            save_config()
        self.send_json({"ok": True, "config": CONFIG})

    def handle_upload(self, data: dict) -> None:
        sid = str(data.get("sessionId") or "")
        if sid not in SESSIONS:
            self.send_json({"error": "对话不存在"}, 400)
            return
        name = str(data.get("name") or "附件")
        mime = str(data.get("mime") or "application/octet-stream")
        payload = data.get("data")
        if not isinstance(payload, str) or not payload:
            self.send_json({"error": "没有文件内容"}, 400)
            return
        if payload.startswith("data:") and "," in payload[:120]:
            payload = payload.split(",", 1)[1]
        try:
            raw = base64.b64decode(payload, validate=False)
        except (ValueError, TypeError) as exc:
            self.send_json({"error": f"文件内容无法读取：{exc}"}, 400)
            return
        saved = save_upload(sid, name, mime, raw)
        self.send_json(saved)

    def serve_attachment(self, session_id: str, file_id: str) -> None:
        meta = load_attachment(session_id, file_id)
        path = Path(meta["path"])
        mime = meta.get("mime") if meta.get("kind") == "image" else "application/octet-stream"
        if mime not in SAFE_IMAGE_MIME:
            mime = "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        if mime == "application/octet-stream":
            self.send_header("Content-Disposition", "attachment")
        self.end_headers()
        self.wfile.write(data)

    def handle_chat(self, data: dict) -> None:
        text = str(data.get("text") or "").strip()
        raw_ids = data.get("attachmentIds") or []
        if raw_ids is None:
            raw_ids = []
        if not isinstance(raw_ids, list):
            self.send_json({"error": "附件格式不对"}, 400)
            return
        if len(raw_ids) > MAX_ATTACHMENTS:
            self.send_json({"error": "一次最多 8 个附件"}, 400)
            return
        if len(text) > 100_000:
            self.send_json({"error": "内容太长"}, 400)
            return
        sid = str(data.get("sessionId") or "")
        if sid not in SESSIONS:
            session = create_session(data.get("cwd"))
            sid = session["id"]
        try:
            attachments = [load_attachment(sid, str(item)) for item in raw_ids]
        except (RuntimeError, ValueError) as exc:
            self.send_json({"error": str(exc), "sessionId": sid}, 400)
            return
        if not text and not attachments:
            self.send_json({"error": "请输入内容或添加文件"}, 400)
            return
        try:
            turn, _snapshot = start_turn(sid, text, attachments)
        except RuntimeError as exc:
            self.send_json({"error": str(exc), "sessionId": sid}, 400)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self.write_chunk({"type": "started", "sessionId": sid})
        try:
            while True:
                item = turn.queue.get()
                if item is None:
                    break
                self.write_chunk(item)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def write_chunk(self, payload: dict) -> None:
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.wfile.write(f"{len(data):X}\r\n".encode("ascii") + data + b"\r\n")
        self.wfile.flush()


def shutdown() -> None:
    time.sleep(0.3)
    for turn in list(TURNS.values()):
        turn.stopped = True
        kill_proc(turn.proc)
    try:
        PORT_FILE.unlink()
    except OSError:
        pass
    log("工作台退出")
    os._exit(0)


def probe_existing() -> int | None:
    if not PORT_FILE.exists():
        return None
    try:
        info = json.loads(PORT_FILE.read_text(encoding="utf-8"))
        port = int(info["port"])
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    try:
        with socket.create_connection(("127.0.0.1", port), 0.5) as sock:
            request = f"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
            sock.sendall(request.encode("ascii"))
            chunks = []
            while True:
                piece = sock.recv(1024)
                if not piece:
                    break
                chunks.append(piece)
                if sum(len(item) for item in chunks) > 4000:
                    break
    except OSError:
        return None
    blob = b"".join(chunks)
    if b"grok-desktop" in blob:
        return port
    return None


def open_window(port: int) -> None:
    url = f"http://127.0.0.1:{port}/"
    edge = find_edge()
    if edge is not None:
        profile = DATA / "edge-profile"
        profile.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            [
                str(edge),
                f"--app={url}",
                "--new-window",
                "--window-size=1360,860",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--disable-features=Translate",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    import webbrowser
    webbrowser.open(url)


def fatal(message: str) -> None:
    log(message)
    windowed = is_frozen() or "pythonw" in Path(sys.executable).name.lower()
    if windowed:
        ctypes.windll.user32.MessageBoxW(0, message, "Grok 工作台", 0x10)
    else:
        print(message, file=sys.stderr)
    raise SystemExit(1)


def self_test() -> None:
    root = ROOT
    got = resolve_under(root, "static")
    assert got == (root / "static").resolve()
    try:
        resolve_under(root, r"..\..")
        raise AssertionError("路径越界未被拦住")
    except ValueError:
        pass
    try:
        resolve_under(root, r"C:\Windows")
        raise AssertionError("绝对路径未被拦住")
    except ValueError:
        pass
    assert summarize_input({"path": "a.py", "query": "x"}) == "a.py"
    prompt = compose_prompt("看这张图", [{
        "kind": "image",
        "name": "屏.png",
        "path": r"C:\temp\屏.png",
    }])
    assert "read_file" in prompt and r"C:\temp\屏.png" in prompt and prompt.endswith("看这张图")
    assert compose_prompt("只要文字", []) == "只要文字"
    try:
        safe_id("../etc")
        raise AssertionError("非法编号未被拦住")
    except ValueError:
        pass
    assert safe_filename(r"..\a<b>.png") == "a_b_.png"
    assert attachment_kind("a.png", "image/png") == "image"
    assert attachment_kind("a.svg", "image/svg+xml") == "file"
    print("self-test ok")


def main() -> None:
    ensure_dirs()
    if "--self-test" in sys.argv:
        self_test()
        return
    load_config()
    save_config()
    load_sessions()
    existing = probe_existing()
    if existing and "--no-open" not in sys.argv:
        open_window(existing)
        return
    if existing and "--no-open" in sys.argv:
        print(f"already running on {existing}")
        return
    try:
        pinned = os.environ.get("GROK_DESKTOP_PORT") or ""
        port_no = int(pinned) if pinned.isdigit() else 0
        server = ThreadingHTTPServer(("127.0.0.1", port_no), Handler)
    except OSError as exc:
        fatal(f"无法启动本地服务：{exc}")
        return
    server.daemon_threads = True
    port = server.server_address[1]
    PORT_FILE.write_text(json.dumps({"port": port, "pid": os.getpid()}), encoding="utf-8")
    log(f"工作台已启动 http://127.0.0.1:{port}/")
    print(f"http://127.0.0.1:{port}/")
    if "--no-open" not in sys.argv:
        threading.Thread(target=open_window, args=(port,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
