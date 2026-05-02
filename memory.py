"""
Memory system — user/group profiles, transcript consolidation.
"""
import json as _json
import os
import threading
from datetime import datetime

from config import STOCK_DATA_DIR
from claude import SESSIONS_DIR, ask_claude

MEMORY_DIR = os.path.join(STOCK_DATA_DIR, "memory")

_sessions: dict[str, dict] = {}
_session_lock = threading.RLock()


def set_session_state(sessions: dict[str, dict], lock: threading.RLock) -> None:
    """Called by main.py at startup to share session state without circular imports."""
    global _sessions, _session_lock
    _sessions = sessions
    _session_lock = lock


_MEMORY_SKELETON = """---
name: {name}
type: {mem_type}
open_id: {open_id}
created: {created}
updated: {updated}
session_count: 0
---

## 使用偏好
（待探索）

## 投资特征
（待探索）

## 关键信息
（暂无）

## 近期话题
（暂无）
"""


def _ensure_memory_dirs() -> None:
    os.makedirs(os.path.join(MEMORY_DIR, "user"), exist_ok=True)
    os.makedirs(os.path.join(MEMORY_DIR, "group"), exist_ok=True)


def _create_memory_skeleton(open_id: str, name: str, mem_type: str) -> str:
    """Create an empty memory file. Returns the file path."""
    _ensure_memory_dirs()
    subdir = "user" if mem_type == "user" else "group"
    path = os.path.join(MEMORY_DIR, subdir, f"{open_id}.md")
    if os.path.exists(path):
        return path
    now = datetime.now().isoformat()
    content = _MEMORY_SKELETON.format(
        name=name, mem_type=mem_type, open_id=open_id,
        created=now, updated=now,
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[记忆] 创建空骨架: {path}", flush=True)
    return path


def _load_memory(conv_id: str, chat_type: str, sender_id: str = "") -> tuple[str, str]:
    """Load memory file content. Returns (content, file_path)."""
    _ensure_memory_dirs()
    if chat_type == "p2p":
        path = os.path.join(MEMORY_DIR, "user", f"{sender_id}.md")
    else:
        path = os.path.join(MEMORY_DIR, "group", f"{conv_id}.md")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read(), path
    return "", path


def _list_modified_transcripts(hours: int = 12) -> list[str]:
    """Return list of session_uuids whose transcript files were modified within N hours."""
    import time
    cutoff = time.time() - hours * 3600
    results = []
    if not os.path.isdir(SESSIONS_DIR):
        return results
    for fname in os.listdir(SESSIONS_DIR):
        if not fname.endswith(".jsonl"):
            continue
        fpath = os.path.join(SESSIONS_DIR, fname)
        if os.path.getmtime(fpath) >= cutoff:
            results.append(fname.replace(".jsonl", ""))
    return results


def _uuid_to_conv(session_uuid: str) -> str | None:
    """Reverse-lookup conv_id from session_uuid."""
    with _session_lock:
        for conv_id, cfg in _sessions.items():
            if cfg.get("session_id") == session_uuid:
                return conv_id
    return None


def _consolidate_session(conv_id: str) -> tuple[bool, str]:
    """Run memory consolidation for a single session. Returns (success, summary)."""
    with _session_lock:
        cfg = _sessions.get(conv_id)
        cfg = dict(cfg) if cfg else None
    if not cfg:
        print(f"[整理] 未找到 session: {conv_id}", flush=True)
        return False, ""

    session_type = cfg.get("type", "")
    session_id = cfg.get("session_id", "")

    if session_type == "dm":
        parts = conv_id.rsplit("_", 1)
        sender_id = parts[-1] if len(parts) >= 2 else conv_id
        mem_path = os.path.join(MEMORY_DIR, "user", f"{sender_id}.md")
    else:
        mem_path = os.path.join(MEMORY_DIR, "group", f"{conv_id}.md")

    transcript_path = os.path.join(SESSIONS_DIR, f"{session_id}.jsonl")
    if not os.path.exists(transcript_path):
        return False, ""

    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        recent = []
        for line in lines[-100:]:
            try:
                entry = _json.loads(line)
                t = entry.get("type", "")
                msg = entry.get("message", {})
                if t == "user" and msg.get("role") == "user":
                    recent.append(f"[用户]: {msg.get('content', '')[:300]}")
                elif t == "assistant" and msg.get("role") == "assistant":
                    c = msg.get("content", "")
                    if isinstance(c, str) and len(c) > 20:
                        recent.append(f"[回复摘要]: {c[:200]}")
            except (_json.JSONDecodeError, KeyError):
                continue
        transcript_sample = "\n".join(recent[-30:])
    except OSError:
        return False, ""

    if not transcript_sample.strip():
        return False, ""

    old_memory = ""
    if os.path.exists(mem_path):
        with open(mem_path, "r", encoding="utf-8") as f:
            old_memory = f.read()

    summary_prompt = (
        f"你是一个记忆整理助手。请基于下方最近的对话记录，更新用户/群聊的记忆文件。\n\n"
        f"现有记忆：\n{old_memory or '(新用户/群，暂无记忆)'}\n\n"
        f"最近对话：\n{transcript_sample}\n\n"
        "请输出更新后的完整记忆文件（Markdown + YAML frontmatter）。\n"
        "规则：\n"
        "1. 保持 frontmatter 中的 name/type/open_id/created 不变，只更新 updated\n"
        "2. 更新「使用偏好」— 从对话中推断用户如何使用助手\n"
        "3. 更新「投资特征」— 风险偏好、关注板块、重要观点\n"
        "4. 更新「近期话题」— 最近 5 条话题摘要，每条一行\n"
        "5. 用户明确要求记住的内容记入「关键信息」\n"
        "6. 记忆文件控制在 600 字以内\n"
        "7. 只输出记忆文件内容，不要任何解释\n"
        "8. 保持 frontmatter 格式: ---\\n...\\n---\\n\\n正文\n"
        "9. 在记忆文件末尾添加一行 `[本次更新]: <一句话总结本次更新了哪些内容>`"
    )

    try:
        result = ask_claude(summary_prompt, timeout=120)
        if result.startswith("---"):
            summary = ""
            for line in result.strip().split("\n"):
                if line.startswith("[本次更新]:"):
                    summary = line.replace("[本次更新]:", "").strip()
                    break
            _ensure_memory_dirs()
            with open(mem_path, "w", encoding="utf-8") as f:
                f.write(result.strip())
            print(f"[整理] 已更新: {mem_path} ({summary})", flush=True)
            return True, summary
        else:
            print(f"[整理] 跳过无效输出: {conv_id[:8]}", flush=True)
            return False, ""
    except (OSError, ValueError, RuntimeError) as e:
        print(f"[整理] 失败 {conv_id[:8]}: {e}", flush=True)
        return False, ""


def _list_group_sessions() -> list[dict]:
    """Return all group sessions with short IDs for /groups command."""
    groups = []
    with _session_lock:
        for conv_id, cfg in _sessions.items():
            if cfg.get("type") == "group":
                short_id = conv_id[-6:] if len(conv_id) >= 6 else conv_id
                groups.append({
                    "short_id": short_id,
                    "conv_id": conv_id,
                    "label": cfg.get("label", short_id),
                    "created_at": cfg.get("created_at", ""),
                })
    return groups


def _read_group_transcript(conv_id: str, limit: int = 30) -> str:
    """Read recent user/assistant messages from a group's Claude Code transcript."""
    with _session_lock:
        cfg = _sessions.get(conv_id)
    if not cfg or cfg["type"] != "group":
        return ""
    transcript_path = os.path.join(SESSIONS_DIR, f"{cfg['session_id']}.jsonl")
    if not os.path.exists(transcript_path):
        return ""
    lines = []
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = _json.loads(line)
                    if entry.get("type") == "user" and entry.get("message", {}).get("role") == "user":
                        content = entry["message"].get("content", "")
                        if content:
                            lines.append(content)
                    elif entry.get("type") == "assistant" and entry.get("message", {}).get("role") == "assistant":
                        content = entry["message"].get("content", "")
                        if content:
                            lines.append(f"[回复]: {content[:200]}")
                except (_json.JSONDecodeError, KeyError):
                    continue
    except OSError:
        return ""
    return "\n".join(f"  {l}" for l in lines[-limit:])
