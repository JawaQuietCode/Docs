"""Offline, fail-closed static scanner for untrusted agent skill bundles.

This is a defense-in-depth heuristic, NOT an antivirus or proof of safety.
It never executes scanned content. All skill text/code is treated as data.
"""
from __future__ import annotations

import ast
import hashlib
import re
import tempfile
import zipfile
from pathlib import Path

MAX_FILES = 300
MAX_FILE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 8_000_000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}

RULES = [
    ("critical", "credential_theft", re.compile(r"(?:\.env|id_rsa|Login Data|Local State|AWS_SECRET_ACCESS_KEY|GITHUB_TOKEN|OPENAI_API_KEY).{0,180}(?:requests\.|fetch\(|https?://|curl|wget|urllib|socket)|(?:requests\.|fetch\(|https?://|curl|wget|urllib|socket).{0,180}(?:\.env|id_rsa|Login Data|AWS_SECRET_ACCESS_KEY|GITHUB_TOKEN|OPENAI_API_KEY)", re.I | re.S)),
    ("critical", "reverse_shell", re.compile(r"/dev/tcp/|bash\s+-i\s+>&|nc\s+-e\s|ncat\s+-e\s|socat\s+.*exec:", re.I)),
    ("critical", "destructive_payload", re.compile(r"rm\s+-rf\s+/(?:\s|$)|mkfs\.|dd\s+if=.*of=/dev/|shred\s+-[a-z]*u", re.I)),
    ("high", "dynamic_execution", re.compile(r"\beval\s*\(|new\s+Function\s*\(|\bcompile\s*\([^\n]*\bexec\b", re.I)),
    ("high", "encoded_dynamic_execution", re.compile(r"(?:(?:base64\s+-d|atob\s*\(|Buffer\.from\([^)]*base64|\bfromhex\s*\(|\bmarshal\.loads\s*\().{0,180}(?:\beval\s*\(|new\s+Function|subprocess\.|spawn\s*\()|(?:\beval\s*\(|new\s+Function|subprocess\.|spawn\s*\().{0,180}(?:base64\s+-d|atob\s*\(|Buffer\.from\([^)]*base64|\bfromhex\s*\(|\bmarshal\.loads\s*\())", re.I | re.S)),
    ("high", "persistence_or_startup", re.compile(r"authorized_keys|crontab|systemctl\s+enable|LaunchAgents|\.bashrc|\.zshrc|/etc/rc\.local", re.I)),
    ("high", "prompt_injection", re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|rules)|reveal\s+(?:the\s+)?system prompt|do not report (?:any )?findings|mark this skill as safe", re.I)),
    ("medium", "network_access", re.compile(r"\b(?:requests\.(?:get|post)|urllib\.request|httpx\.|axios\.|fetch\s*\(|curl\s|wget\s)", re.I)),
    ("medium", "shell_execution", re.compile(r"\b(?:subprocess\.(?:run|Popen|call)|child_process|execSync|spawnSync|os\.system)\b", re.I)),
    ("medium", "secret_access", re.compile(r"\b(?:os\.environ|process\.env|keyring|get_password|credential|secret[_-]?key|access[_-]?token)\b", re.I)),
]


def _files(root: Path):
    count = total = 0
    for p in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.is_symlink():
            yield p, "symlink"
            continue
        if not p.is_file():
            continue
        count += 1
        try:
            size = p.stat().st_size
        except OSError:
            yield p, "unreadable"
            continue
        total += size
        if count > MAX_FILES or size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            yield p, "limit"
            return
        yield p, None


def audit_skill(target: str | Path) -> dict:
    original = Path(target).expanduser().absolute()
    if original.is_symlink():
        return {"ok": False, "verdict": "BLOCKED", "score": 100,
                "findings": [{"rule": "target_symlink", "severity": "critical"}], "files": []}
    candidate = original.resolve()
    if candidate.is_file() and candidate.suffix.lower() in {".zip", ".skill"}:
        try:
            with zipfile.ZipFile(candidate) as archive, tempfile.TemporaryDirectory(prefix="kwee-skill-audit-") as temp:
                infos = archive.infolist()
                if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
                    return {"ok": False, "verdict": "BLOCKED", "score": 100,
                            "findings": [{"rule": "archive_size_limit", "severity": "critical"}], "files": []}
                base = Path(temp).resolve()
                for info in infos:
                    normalized_name = info.filename.replace("\\", "/")
                    member = Path(normalized_name)
                    mode = (info.external_attr >> 16) & 0o170000
                    drive_like = bool(re.match(r"^[A-Za-z]:", normalized_name))
                    if member.is_absolute() or normalized_name.startswith("/") or drive_like or ".." in member.parts or mode == 0o120000:
                        return {"ok": False, "verdict": "BLOCKED", "score": 100,
                                "findings": [{"rule": "unsafe_archive_path_or_symlink", "severity": "critical", "file": info.filename}], "files": []}
                    dest = (base / member).resolve()
                    dest.relative_to(base)
                    if info.is_dir():
                        dest.mkdir(parents=True, exist_ok=True)
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(info) as src, dest.open("wb") as dst:
                            dst.write(src.read(MAX_FILE_BYTES + 1))
                report = audit_skill(base)
                report["archive"] = candidate.name
                return report
        except Exception as exc:
            return {"ok": False, "verdict": "BLOCKED", "score": 100,
                    "findings": [{"rule": "archive_invalid", "severity": "critical", "evidence": type(exc).__name__}], "files": []}
    root = candidate
    findings = []
    manifest = []
    total = 0
    if not root.exists() or not (root.is_dir() or root.is_file()):
        return {"ok": False, "verdict": "BLOCKED", "score": 100, "findings": [{"rule": "target_missing", "severity": "critical"}], "files": []}
    if root.is_symlink():
        return {"ok": False, "verdict": "BLOCKED", "score": 100,
                "findings": [{"rule": "target_symlink", "severity": "critical"}], "files": []}
    if root.is_file():
        paths = [root]
    else:
        walked = list(_files(root))
        paths = [p for p, issue in walked if issue is None]
        for p, issue in walked:
            if issue:
                findings.append({"rule": f"filesystem_{issue}", "severity": "high", "file": str(p.relative_to(root))})
    for p in paths:
        try:
            raw = p.read_bytes()
            total += len(raw)
            if len(raw) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                findings.append({"rule": "size_limit", "severity": "high", "file": p.name})
                continue
            digest = hashlib.sha256(raw).hexdigest()
            manifest.append({"file": str(p.relative_to(root)) if root.is_dir() else p.name, "sha256": digest, "bytes": len(raw)})
            # Native executable payloads have no legitimate place in a text skill bundle.
            executable_magic = (raw.startswith(b"MZ") or raw.startswith(b"\x7fELF")
                                or raw.startswith(b"\x00asm")
                                or raw[:4] in {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe"})
            executable_ext = p.suffix.lower() in {".exe", ".dll", ".so", ".dylib", ".scr", ".com", ".msi", ".apk", ".wasm"}
            if executable_magic or executable_ext:
                findings.append({"rule": "native_executable_payload", "severity": "critical", "file": str(p.relative_to(root)) if root.is_dir() else p.name})
            if p.suffix.lower() in {".zip", ".7z", ".rar", ".tar", ".gz", ".whl", ".jar"}:
                findings.append({"rule": "nested_archive_uninspected", "severity": "high", "file": str(p.relative_to(root)) if root.is_dir() else p.name})
            text = raw.decode("utf-8", errors="replace")
        except OSError:
            findings.append({"rule": "file_unreadable", "severity": "high", "file": p.name})
            continue
        rel = str(p.relative_to(root)) if root.is_dir() else p.name
        if p.suffix.lower() in {".py", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ps1", ".bat", ".cmd", ".html", ".htm"}:
            for severity, rule, pattern in RULES:
                for m in pattern.finditer(text):
                    findings.append({"rule": rule, "severity": severity, "file": rel,
                                     "line": text.count("\n", 0, m.start()) + 1,
                                     "evidence": text[max(0, m.start()-45):min(len(text), m.end()+45)].replace("\n", " ")[:180]})
        elif p.suffix.lower() in {".md", ".txt", ".rst"}:
            for severity, rule, pattern in RULES:
                if rule == "prompt_injection":
                    for m in pattern.finditer(text):
                        findings.append({"rule": rule, "severity": "critical", "file": rel,
                                         "line": text.count("\n", 0, m.start()) + 1,
                                         "evidence": m.group(0)[:140]})
        if p.suffix.lower() == ".py":
            try:
                tree = ast.parse(text, filename=rel)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        name = (ast.unparse(node.func) if hasattr(ast, "unparse") else "")
                        if name in {"os.system", "subprocess.Popen", "subprocess.run", "eval", "exec"}:
                            # Report structural evidence; regex may also find it.
                            if not any(f.get("file") == rel and f.get("line") == getattr(node, "lineno", 0) for f in findings):
                                findings.append({"rule": "python_dangerous_call", "severity": "medium", "file": rel, "line": getattr(node, "lineno", 0), "evidence": name})
            except SyntaxError as exc:
                findings.append({"rule": "python_parse_error", "severity": "medium", "file": rel, "line": exc.lineno or 0})
    weights = {"critical": 45, "high": 22, "medium": 8, "low": 2}
    # Repeated benign API use must not inflate one medium signal into CRITICAL.
    unique_rules = {}
    for finding in findings:
        unique_rules[finding["rule"]] = max(unique_rules.get(finding["rule"], 0), weights.get(finding["severity"], 0))
    repeats = max(0, len(findings) - len(unique_rules))
    score = min(100, sum(unique_rules.values()) + min(20, repeats * 2))
    verdict = "BLOCKED" if any(f["severity"] == "critical" for f in findings) or score >= 45 else "REVIEW" if score >= 8 else "LOW_RISK"
    return {"ok": verdict == "LOW_RISK", "verdict": verdict, "score": score,
            "files_scanned": len(manifest), "bytes_scanned": total,
            "findings": findings, "files": manifest,
            "limitations": ["Static heuristic only; not antivirus.", "Does not execute code or detect every obfuscated/runtime payload.", "LOW_RISK is not a safety guarantee."]}
