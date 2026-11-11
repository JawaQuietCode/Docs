"""client.py — klien LLM OpenAI-compatible.

Desain
------
* **Satu httpx.Client** dipakai ulang (keep-alive + pooling). Dulu setiap
  panggilan membuat klien baru: TLS handshake ulang dan tidak ada koneksi
  hangat — mahal untuk turn agentic yang bisa 50–500 panggilan.
* **Retry berjenjang** untuk gangguan sesaat (408/409/425/429/5xx + gangguan
  jaringan). Dulu satu 502 dari gateway = model itu langsung dinyatakan gagal.
* **Streaming opsional** (`llm.stream: true`): respons diakumulasi dari delta,
  jadi request panjang tidak menggantung sampai batas waktu. Bila gateway
  menolak streaming, otomatis jatuh ke mode non-stream.
* **Model cadangan (fallback)**: `LLM_MODEL` dulu, lalu `LLM_FALLBACK_MODELS`.
  Anggaran waktu DIBAGI antar model — dulu model pertama menghabiskan seluruh
  sisa waktu sehingga cadangan hanya kebagian ~1 detik.
* **Circuit breaker**: model yang gagal 3x berurutan diistirahatkan 5 menit,
  supaya turn berikutnya tidak membuang waktu di model yang sedang rusak.
* **usage** (`prompt_tokens`/`completion_tokens`) disimpan di `last_usage` —
  satu-satunya cara mengukur biaya turn dan mendeteksi konteks yang membengkak.
* **Kesalahan terstruktur**: `LLMError` membawa `status`, `code`, `retryable`;
  `ContextOverflowError` dipisahkan supaya pemanggil bisa memadatkan riwayat
  alih-alih menurunkan `max_tokens` (yang tidak menolong sama sekali).

Catatan model: gateway bisa menolak satu model tertentu tanpa alasan jelas.
Contoh nyata: `cbai/deepseek-v4.1-flash` menolak bahkan payload minimal
(`model_param_invalid`), sedangkan `cbcn/deepseek-v4.1-flash` (model sama,
prefix beda) berjalan normal. Karena itu fallback model dipertahankan.
"""
import base64
import json
import os
import re
import threading
import time

import httpx

# --------------------------------------------------------------------------- #
# Kesalahan
# --------------------------------------------------------------------------- #


class LLMError(Exception):
    """Kegagalan panggilan LLM (membawa konteks yang bisa dipakai pemanggil)."""

    def __init__(self, msg, *, model="", status=0, code="", retryable=False):
        super().__init__(msg)
        self.model = model
        self.status = int(status or 0)
        self.code = str(code or "")
        self.retryable = bool(retryable)


class ContextOverflowError(LLMError):
    """Riwayat/prompt melebihi jendela konteks model → perlu pemadatan."""


_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_CONTEXT_HINTS = re.compile(
    r"context[_ ]length|context window|prompt is too long|input .{0,20}too long|"
    r"maximum context|too many tokens|exceeds? .{0,15}tokens", re.I)
# Parameter yang tidak diterima sebagian model reasoning.
_NO_TEMPERATURE = re.compile(r"^(o[134]|gpt-5|gpt5|.*-reasoning|.*thinking)", re.I)

# Breaker: model → {"fails": n, "open_until": ts}
_BREAKER: dict = {}
_BREAKER_FAILS = 3
_BREAKER_COOLDOWN = 300.0

# Klien HTTP bersama (pooling). http2 hanya bila paket `h2` tersedia.
_CLIENT = None
_RATE_LOCK = threading.Lock()
_RATE_LAST: dict[str, float] = {}


def _pace_request(key: str, interval: float) -> float:
    """Serialize provider requests and enforce a minimum start-to-start interval.

    Returns the number of seconds slept. A single process-wide lock makes this
    effective across chats/threads; separate provider base URLs have separate
    clocks. Set interval=0 to disable.
    """
    interval = max(0.0, float(interval or 0))
    if not interval:
        return 0.0
    slept = 0.0
    with _RATE_LOCK:
        now = time.monotonic()
        previous = _RATE_LAST.get(str(key), 0.0)
        delay = max(0.0, interval - (now - previous))
        if delay:
            time.sleep(delay)
            slept = delay
        _RATE_LAST[str(key)] = time.monotonic()
    return slept


def _client() -> httpx.Client:
    global _CLIENT
    if _CLIENT is None:
        limits = httpx.Limits(max_keepalive_connections=8, max_connections=16,
                              keepalive_expiry=60.0)
        try:
            _CLIENT = httpx.Client(http2=True, limits=limits)
        except Exception:
            _CLIENT = httpx.Client(limits=limits)
    return _CLIENT


def close_client() -> None:
    """Tutup klien bersama (dipanggil saat shutdown)."""
    global _CLIENT
    if _CLIENT is not None:
        try:
            _CLIENT.close()
        except Exception:
            pass
        _CLIENT = None
_RATE_LOCK = threading.Lock()
_RATE_LAST: dict[str, float] = {}


def _pace_request(key: str, interval: float) -> float:
    """Serialize provider requests and enforce a minimum start-to-start interval.

    Returns the number of seconds slept. A single process-wide lock makes this
    effective across chats/threads; separate provider base URLs have separate
    clocks. Set interval=0 to disable.
    """
    interval = max(0.0, float(interval or 0))
    if not interval:
        return 0.0
    slept = 0.0
    with _RATE_LOCK:
        now = time.monotonic()
        previous = _RATE_LAST.get(str(key), 0.0)
        delay = max(0.0, interval - (now - previous))
        if delay:
            time.sleep(delay)
            slept = delay
        _RATE_LAST[str(key)] = time.monotonic()
    return slept


def _short_error(text: str, limit: int = 300) -> str:
    """Ringkas pesan error gateway agar enak dibaca di WhatsApp."""
    raw = (text or "").strip()
    # Gateway sering membungkus error asli sebagai JSON di dalam string.
    try:
        outer = json.loads(raw)
        msg = (outer.get("error") or {}).get("message") or raw
    except Exception:
        msg = raw
    # Coba bedah payload dalam (mis. "[400]: {\"code\":11133,...}").
    if isinstance(msg, str) and "]: " in msg:
        head, _, tail = msg.partition("]: ")
        try:
            inner = json.loads(tail)
            code = (inner.get("extError") or {}).get("code") or inner.get("code") or ""
            detail = inner.get("msg") or (inner.get("extError") or {}).get("message") or ""
            bits = [b for b in (head.strip(" []"), code, detail) if b]
            msg = " ".join(bits) or msg
        except Exception:
            pass
    msg = " ".join(str(msg).split())
    return msg[:limit] + ("…" if len(msg) > limit else "")


def _classify(model: str, status: int, body: str) -> LLMError:
    """Terjemahkan respons error gateway jadi LLMError yang tepat jenisnya."""
    short = _short_error(body)
    low = (body or "").lower()
    if _CONTEXT_HINTS.search(low):
        return ContextOverflowError(
            f"HTTP {status}: konteks melebihi jendela model ({short})",
            model=model, status=status, code="context_overflow", retryable=False)
    return LLMError(f"HTTP {status}: {short}", model=model, status=status,
                    code="http_error", retryable=status in _RETRY_STATUS)


def _breaker_open(model: str) -> bool:
    st = _BREAKER.get(model) or {}
    return time.time() < float(st.get("open_until") or 0)


def _breaker_fail(model: str) -> None:
    st = _BREAKER.setdefault(model, {"fails": 0, "open_until": 0.0})
    st["fails"] = int(st.get("fails") or 0) + 1
    if st["fails"] >= _BREAKER_FAILS:
        st["open_until"] = time.time() + _BREAKER_COOLDOWN
        st["fails"] = 0


def _breaker_ok(model: str) -> None:
    _BREAKER.pop(model, None)


# --------------------------------------------------------------------------- #
# Vision: perkecil gambar sebelum dikirim
# --------------------------------------------------------------------------- #


def _shrink_image(url: str, max_px: int = 1568, quality: int = 80) -> str:
    """Perkecil gambar lokal/data-URL sebelum dikirim ke model.

    Gambar 8 MB dari WhatsApp menjadi data-URL ~11 MB: gateway menolak atau
    timeout. Dengan sisi terpanjang ≤1568px + JPEG q80, ukurannya turun 5–10x
    tanpa kehilangan detail yang berarti untuk analisis.
    """
    try:
        from PIL import Image
    except Exception:
        return url
    raw = None
    is_data = url.startswith("data:")
    try:
        if is_data:
            header, _, b64 = url.partition(",")
            if "base64" not in header:
                return url
            raw = base64.b64decode(b64)
        elif os.path.exists(url):
            with open(url, "rb") as fh:
                raw = fh.read()
        else:
            return url
        import io
        img = Image.open(io.BytesIO(raw))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        scale = min(1.0, float(max_px) / float(max(w, h) or 1))
        if scale < 1.0:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        out = buf.getvalue()
        if len(out) >= len(raw):
            return url
        return "data:image/jpeg;base64," + base64.b64encode(out).decode("ascii")
    except Exception:
        return url


# --------------------------------------------------------------------------- #
# Klien
# --------------------------------------------------------------------------- #


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: int = 60, temperature: float = 0.7, max_tokens: int = 2048,
                 fallback_models=None, stream: bool = True, retry_max: int = 3,
                 vision_max_px: int = 1568, request_interval_seconds: float = 10.0):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.stream = bool(stream)
        self.retry_max = max(1, int(retry_max or 1))
        self.vision_max_px = int(vision_max_px or 1568)
        self.request_interval_seconds = max(0.0, float(request_interval_seconds or 0))
        # Buang duplikat & model utama dari daftar cadangan.
        seen = {self.model}
        self.fallback_models = []
        for m in (fallback_models or []):
            m = (m or "").strip()
            if m and m not in seen:
                seen.add(m)
                self.fallback_models.append(m)
        self.last_model = self.model
        self.last_usage = {}
        # Aggregate actual usage across all successful API calls in this turn.
        # Public-user credit accounting consumes this after the turn completes.
        self.total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _record_usage(self, usage):
        if not isinstance(usage, dict):
            return
        self.last_usage = dict(usage)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            try:
                self.total_usage[key] += max(0, int(usage.get(key) or 0))
            except (TypeError, ValueError):
                pass

    @staticmethod
    def _estimated_input_tokens(messages, tools=None):
        """Conservative UTF-8 prompt/tool estimate with bounded image cost."""
        image_count = 0

        def compact(value):
            nonlocal image_count
            if isinstance(value, str) and value.startswith("data:image/") and ";base64," in value:
                image_count += 1
                return "[image payload omitted]"
            if isinstance(value, dict):
                return {k: compact(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [compact(v) for v in value]
            return value

        payload = {"messages": compact(messages or []), "tools": compact(tools or [])}
        byte_count = len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
        # Conservative reserve per image; actual provider usage replaces this
        # estimate when the gateway reports token counts.
        return max(1, byte_count + image_count * 8192)

    @staticmethod
    def _estimated_usage(messages, completion, tools=None):
        """Conservative fallback when a compatible gateway omits usage fields.

        UTF-8 byte counts intentionally overestimate many natural-language
        tokenizers; this is preferable to granting unmetered public requests.
        Provider-reported values always take precedence.
        """
        prompt_bytes = LLMClient._estimated_input_tokens(messages, tools)
        completion_bytes = len(json.dumps(completion or {}, ensure_ascii=False, default=str).encode("utf-8"))
        prompt = max(1, prompt_bytes)
        output = max(0, completion_bytes)
        return {"prompt_tokens": prompt, "completion_tokens": output,
                "total_tokens": prompt + output, "estimated": True}

    @property
    def models(self):
        return [self.model] + self.fallback_models if self.model else list(self.fallback_models)

    # ---------------------------------------------------------------- helpers
    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _body(self, model, messages, tools, max_tokens=None, stream=False,
              tool_choice=None) -> dict:
        body = {
            "model": model,
            "messages": messages,
            "stream": bool(stream),
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
        }
        if stream:
            body["stream_options"] = {"include_usage": True}
        # Model reasoning menolak `temperature`; jangan kirim bila tak didukung.
        if not _NO_TEMPERATURE.match(model or ""):
            body["temperature"] = self.temperature
        if tools:
            body["tools"] = tools
            # `tool_choice="required"` memaksa model MEMANGGIL tool, bukan
            # menulis perintah di balasan. Dipakai loop saat model kedapatan
            # menyerahkan perintah ke pengguna. Hanya dikirim bila ada tools.
            if tool_choice:
                body["tool_choice"] = tool_choice
        return body

    def _url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _post_once(self, model: str, messages, tools, max_tokens=None, timeout=None,
                   tool_choice=None):
        """Satu percobaan non-stream, dengan retry untuk gangguan sesaat."""
        body = self._body(model, messages, tools, max_tokens=max_tokens, stream=False,
                          tool_choice=tool_choice)
        last: LLMError | None = None
        for attempt in range(self.retry_max):
            _pace_request(self.base_url, self.request_interval_seconds)
            try:
                resp = _client().post(self._url(), headers=self._headers(), json=body,
                                      timeout=timeout or self.timeout)
            except httpx.HTTPError as exc:
                last = LLMError(f"tidak bisa dihubungi: {exc}", model=model, retryable=True)
                if attempt + 1 >= self.retry_max:
                    raise last
                time.sleep(min(2 ** attempt, 8))
                continue
            if resp.status_code < 400:
                data = resp.json()
                if isinstance(data.get("usage"), dict):
                    self._record_usage(data["usage"])
                else:
                    choice = (data.get("choices") or [{}])[0]
                    self._record_usage(self._estimated_usage(messages, choice.get("message") or {}, body.get("tools")))
                return data
            err = _classify(model, resp.status_code, resp.text)
            last = err
            if not err.retryable or attempt + 1 >= self.retry_max:
                raise err
            wait = 0.0
            try:
                wait = float(resp.headers.get("retry-after") or 0)
            except Exception:
                wait = 0.0
            time.sleep(wait or min(2 ** attempt, 8))
        raise last or LLMError("gagal mengirim permintaan", model=model)

    def _post_stream(self, model: str, messages, tools, max_tokens=None, timeout=None,
                     tool_choice=None):
        """Satu percobaan streaming; delta diakumulasi jadi satu respons utuh."""
        body = self._body(model, messages, tools, max_tokens=max_tokens, stream=True,
                          tool_choice=tool_choice)
        content_parts, reasoning_parts = [], []
        tool_calls: dict = {}
        finish = None
        usage = {}
        _pace_request(self.base_url, self.request_interval_seconds)
        with _client().stream("POST", self._url(), headers=self._headers(),
                              json=body, timeout=timeout or self.timeout) as resp:
            if resp.status_code >= 400:
                resp.read()
                err = _classify(model, resp.status_code, resp.text)
                try:
                    err.retry_after = float(resp.headers.get("retry-after") or 0)
                except (TypeError, ValueError):
                    err.retry_after = 0.0
                raise err
            for line in resp.iter_lines():
                if not line:
                    continue
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload in ("[DONE]", ""):
                    continue
                try:
                    obj = json.loads(payload)
                except Exception:
                    continue
                if isinstance(obj.get("usage"), dict):
                    usage = obj["usage"]
                choices = obj.get("choices") or []
                if not choices:
                    continue
                ch = choices[0] or {}
                delta = ch.get("delta") or {}
                if delta.get("content"):
                    content_parts.append(delta["content"])
                if delta.get("reasoning_content"):
                    reasoning_parts.append(delta["reasoning_content"])
                for tc in delta.get("tool_calls") or []:
                    idx = int(tc.get("index") or 0)
                    slot = tool_calls.setdefault(idx, {"id": "", "type": "function",
                                                       "function": {"name": "", "arguments": ""}})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
        message = {
            "content": "".join(content_parts) or None,
            "reasoning_content": "".join(reasoning_parts) or None,
            "tool_calls": [tool_calls[i] for i in sorted(tool_calls)] or None,
        }
        if usage:
            self._record_usage(usage)
        else:
            self._record_usage(self._estimated_usage(messages, message, tools))
        return {"choices": [{"message": message, "finish_reason": finish}]}

    @staticmethod
    def _looks_like_token_error(text: str) -> bool:
        """True bila error gateway tampak soal `max_tokens` yang terlalu besar.

        HARUS spesifik: hanya bila pesan benar-benar menyebut token. Error
        generik seperti `model_param_invalid` BUKAN soal token (bisa model salah),
        jadi tidak boleh memicu penurunan `max_tokens` — kalau tidak, model yang
        memang rusak akan dicoba ulang berkali-kali tanpa guna.

        CATATAN: `context_length_exceeded` adalah masalah INPUT (riwayat terlalu
        panjang), bukan `max_tokens` — itu ditangani `ContextOverflowError` dan
        TIDAK boleh memicu penurunan di sini.
        """
        low = (text or "").lower()
        if _CONTEXT_HINTS.search(low):
            return False
        if "max_tokens" in low or "max tokens" in low or "maxtokens" in low:
            return True
        return "token" in low and any(
            k in low for k in ("too large", "exceed", "out of range", "maximum", "limit"))

    def _post_with_graceful_tokens(self, model: str, messages, tools, timeout=None,
                                   max_tokens=None, tool_choice=None):
        """Kirim permintaan; bila gagal karena `max_tokens`, turunkan lalu ulangi.

        Menjaga bot tetap jalan di gateway yang plafon tokennya lebih rendah
        dari nilai konfigurasi. Penurunan bertahap: nilai konfigurasi → 4096 →
        2048 → 1024. Error non-token langsung dilempar (ditangani pemanggil).
        """
        base = self.max_tokens if max_tokens is None else max_tokens
        candidates = []
        for cap in (base, 4096, 2048, 1024):
            if cap and cap not in candidates and cap <= (base or cap):
                candidates.append(cap)
        last_exc = None
        for cap in candidates:
            try:
                if self.stream:
                    stream_attempt = 0
                    while True:
                        try:
                            return self._post_stream(model, messages, tools,
                                                     max_tokens=cap, timeout=timeout,
                                                     tool_choice=tool_choice)
                        except LLMError as exc:
                            if not exc.retryable or stream_attempt + 1 >= self.retry_max:
                                raise
                            wait = float(getattr(exc, "retry_after", 0) or min(2 ** stream_attempt, 30))
                            stream_attempt += 1
                            print(f"[llm] HTTP {exc.status}; retry {stream_attempt}/{self.retry_max - 1} in {wait:.1f}s", flush=True)
                            time.sleep(max(0.0, wait))
                        except Exception as exc:  # noqa: BLE001
                            # Gateway/versi httpx tidak mendukung streaming: matikan
                            # streaming untuk proses ini, lalu pakai jalur non-stream.
                            self.stream = False
                            print(f"[llm] streaming tidak didukung ({exc}); pakai non-stream",
                                  flush=True)
                            return self._post_once(model, messages, tools,
                                                   max_tokens=cap, timeout=timeout,
                                                   tool_choice=tool_choice)
                return self._post_once(model, messages, tools,
                                       max_tokens=cap, timeout=timeout,
                                       tool_choice=tool_choice)
            except ContextOverflowError:
                raise
            except LLMError as exc:
                last_exc = exc
                if not self._looks_like_token_error(str(exc)):
                    raise
                continue
        raise last_exc if last_exc else LLMError("gagal mengirim permintaan", model=model)

    # ------------------------------------------------------------------- chat
    def chat(self, messages, tools=None, timeout=None, max_tokens=None,
             tool_choice=None):
        """Kembalikan {"content", "tool_calls", "model", "finish_reason", "usage"}.

        Mencoba model utama lalu cadangannya. Bila semua gagal, melempar
        LLMError berisi ringkasan percobaan.

        `timeout` opsional membatasi SELURUH percobaan (detik) dan dibagi rata
        antar model, supaya model cadangan tidak hanya kebagian sisa 1 detik.
        """
        if not self.base_url:
            raise LLMError("LLM_BASE_URL belum diisi di .env")
        if not self.models:
            raise LLMError("LLM_MODEL belum diisi di .env")

        guard = getattr(self, "request_guard", None)
        if callable(guard):
            guard(messages, self.max_tokens if max_tokens is None else max_tokens, tools)

        deadline = (time.time() + float(timeout)) if timeout else None
        errors = []
        # Model yang sedang diistirahatkan breaker dicoba paling akhir.
        models = [m for m in self.models if not _breaker_open(m)]
        rested = [m for m in self.models if _breaker_open(m)]
        if not models:                      # semua sedang istirahat → tetap coba
            models, rested = list(self.models), []
        ordered = models + rested
        for i, model in enumerate(ordered):
            left = len(ordered) - i
            if deadline is not None:
                remain = deadline - time.time()
                if remain <= 1:
                    errors.append(f"{model} → anggaran waktu habis")
                    break
                per_try = max(5.0, remain / max(1, left))
            else:
                per_try = None
            try:
                guard = getattr(self, "request_guard", None)
                if callable(guard):
                    guard(messages, self.max_tokens if max_tokens is None else max_tokens)
                data = self._post_with_graceful_tokens(model, messages, tools,
                                                       timeout=per_try, max_tokens=max_tokens,
                                                       tool_choice=tool_choice)
            except ContextOverflowError:
                raise
            except LLMError as exc:
                _breaker_fail(model)
                errors.append(f"{model} → {exc}")
                continue
            except Exception as exc:  # noqa: BLE001
                _breaker_fail(model)
                errors.append(f"{model} → tidak bisa dihubungi: {exc}")
                continue

            try:
                msg = data["choices"][0]["message"]
            except (KeyError, IndexError, TypeError):
                _breaker_fail(model)
                errors.append(f"{model} → bentuk respons tidak dikenal")
                continue

            _breaker_ok(model)
            self.last_model = model
            return {
                "content": msg.get("content"),
                "tool_calls": msg.get("tool_calls"),
                "model": model,
                # Sebagian model reasoning menaruh "pikiran" di sini dan
                # meninggalkan `content` kosong saat kehabisan token. loop.py
                # memakainya sebagai jaring supaya turn tidak balik hampa.
                "reasoning_content": msg.get("reasoning_content"),
                # "stop" | "length" | "tool_calls" | "content_filter" | None.
                # "length" = model kehabisan token → balasan TERPOTONG. loop.py
                # memakainya untuk melanjutkan (auto-continue) alih-alih
                # mengirim kode/teks yang terputus di tengah.
                "finish_reason": (data.get("choices") or [{}])[0].get("finish_reason"),
                "usage": dict(self.last_usage or {}),
            }

        raise LLMError("semua model gagal: " + " | ".join(errors))

    # ------------------------------------------------------------------ vision
    def vision(self, image_url: str, prompt: str = "Jelaskan gambar ini.",
               models=None, timeout=None, max_tokens=None):
        """Analisis GAMBAR (multimodal). Kembalikan teks deskripsi/jawaban.

        `image_url` boleh berupa URL http(s) ATAU data-URL (`data:image/png;base64,…`)
        ATAU path berkas lokal — jadi tool `vision` bisa mengirim berkas lokal
        tanpa mengunggahnya ke mana pun. Gambar diperkecil dulu (`_shrink_image`)
        supaya tidak ditolak gateway karena terlalu besar.
        """
        if not self.base_url:
            raise LLMError("LLM_BASE_URL belum diisi di .env")
        cand = []
        for m in (models or []) + self.models:
            m = (m or "").strip()
            if m and m not in cand:
                cand.append(m)
        if not cand:
            raise LLMError("tidak ada model untuk vision")
        prepared = _shrink_image(image_url, self.vision_max_px)
        content = [
            {"type": "text", "text": prompt or "Jelaskan gambar ini."},
            {"type": "image_url", "image_url": {"url": prepared}},
        ]
        messages = [{"role": "user", "content": content}]
        guard = getattr(self, "request_guard", None)
        errors = []
        for model in cand:
            try:
                if callable(guard):
                    guard(messages, max_tokens or 2048, None)
                data = self._post_once(model, messages, None,
                                       max_tokens=max_tokens or 2048, timeout=timeout)
            except LLMError as exc:
                if getattr(exc, "code", "") == "public_credit_exhausted":
                    raise
                errors.append(f"{model} → {exc}")
                continue
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{model} → {exc}")
                continue
            try:
                msg = data["choices"][0]["message"]
            except (KeyError, IndexError, TypeError):
                errors.append(f"{model} → bentuk respons tidak dikenal")
                continue
            text = (msg.get("content") or "").strip()
            if not text:
                # Model "menerima" gambar tetapi tidak menghasilkan teks —
                # biasanya model ini TIDAK benar-benar multimodal (mis. model
                # reasoning yang tokennya habis di `reasoning_content`, atau
                # model teks murni). JANGAN kembalikan kosong: coba model
                # berikutnya supaya vision tidak balik hampa.
                rc = (msg.get("reasoning_content") or "").strip()
                errors.append(
                    f"{model} → balasan kosong"
                    + (" (habis di reasoning_content)" if rc else ""))
                continue
            self.last_model = model
            return {"text": text, "model": model}
        raise LLMError("vision gagal: " + " | ".join(errors))
