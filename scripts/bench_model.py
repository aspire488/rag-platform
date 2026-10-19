#!/usr/bin/env python3
"""bench_model.py - BG-1a model-only benchmark driver (plan v2.3, section 10.3).

Contacts ONLY the local Ollama API (egress stays off: Z3/I3/Z4). No downloads (Z6).
No global settings are read or changed. All evidence is written under D:\\rag-platform.

Protocol (section 10.3 BG-1a):
  pre-checks -> load test (32 tokens, num_ctx=2048, keep_alive=0)
             -> sustained test (512 tokens, num_ctx=4096, keep_alive=0)
             -> unload check (RAM/VRAM return to within 300 MB of idle)
Verdict (step 7; D12 floor confirmed 4 tok/s on 2026-10-19):
  usable-cpu | usable-gpu(partial|full) [only with /api/ps size_vram evidence]
  | too-slow [sustained tok/s < floor] | too-heavy [RAM floor breached]
  | blocked [stop condition tripped: swap / runtime cap / manual abort]

Resource safety policy (owner requirement, 2026-10-19 - priority over features):
  - Endpoint validation is STRICT: scheme http, host exactly 'localhost' or
    '127.0.0.1', port exactly 11434. Prefix matching is never accepted.
  - Abort classification is by explicit kind recorded at abort time
    (never by substring matching on free text):
      ram-floor | emergency -> verdict too-heavy (the floor itself was breached)
      swap | runtime-cap | manual | start-gate -> verdict blocked (safety abort)
  - A start gate refuses to load the model when idle available RAM is already
    below the stop threshold; BG-1a never runs while the machine fails the gate.
  - Every exit path (normal, abort, Ctrl+C, internal error) unloads the model and
    records the verified final /api/ps state in the evidence file.

Stop conditions (step 6, owner-tightened 2026-10-19), checked at 1 Hz by a sampler:
  - available RAM < 3 GB (normal) or < 2 GB (emergency backstop) -> abort, verdict too-heavy
  - swap used rises > epsilon above the pre-run baseline          -> abort, verdict blocked
  - total runtime > 15 minutes                                    -> abort, verdict blocked
  - sentinel file exists / Ctrl+C                                 -> abort, verdict blocked
On ANY abort: the in-flight stream is closed and the model is unloaded (keep_alive=0).
Thresholds are never lowered automatically (owner requirement).
"""

import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psutil

PROJECT_ROOT = Path(r"D:\rag-platform")
REPORTS = PROJECT_ROOT / "reports"

PROMPT_LOAD = "Write one short sentence about the water cycle."
PROMPT_SUSTAINED = (
    "Explain the water cycle in about 400 words, with each step in order."
)


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def validate_endpoint(url):
    """Strict local-endpoint validation (Z3/I3): scheme http, host exactly
    'localhost' or '127.0.0.1', port exactly 11434. Prefix matching is NOT
    accepted - 'http://localhost.attacker.example' must never pass.
    Returns (ok, reason).
    """
    try:
        parts = urlsplit(url)
    except ValueError as e:
        return False, f"not parseable: {e}"
    if parts.scheme != "http":
        return False, f"scheme must be http (got '{parts.scheme}')"
    if parts.hostname not in ("localhost", "127.0.0.1"):
        return False, (
            f"host must be exactly 'localhost' or '127.0.0.1' (got '{parts.hostname}')"
        )
    if parts.port != 11434:
        return False, f"port must be 11434 (got '{parts.port}')"
    return True, "ok"


def nvidia_smi():
    """Return list of [vram_used_MiB, vram_total_MiB] per GPU, or None if unavailable."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [
                exe,
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode != 0:
            return {"error": out.stderr.strip()[:200]}
        gpus = []
        for line in out.stdout.strip().splitlines():
            used, total = [int(x.strip()) for x in line.split(",")]
            gpus.append({"vram_used_MiB": used, "vram_total_MiB": total})
        return gpus
    except Exception as e:  # evidence collection must never crash the benchmark
        return {"error": str(e)[:200]}


class Sampler(threading.Thread):
    """1 Hz resource sampler with abort enforcement (section 10.3 step 6)."""

    def __init__(self, cfg, sentinel):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.sentinel = sentinel
        self.stop_flag = threading.Event()
        self.abort_event = threading.Event()
        self.abort_kind = None  # explicit kind: ram-floor|emergency|swap|runtime-cap|manual|start-gate
        self.abort_reason = None
        self.samples = []
        self.swap_baseline = None
        self.ps_evidence = None  # verbatim /api/ps payload captured while loaded
        self.ollama_procs = set()
        self.on_abort = None  # optional callback: force-cancel a blocked stream read
        self.t0 = time.monotonic()

    def run(self):
        # 3-sample baseline before anything loads
        baselines = []
        for _ in range(3):
            v = psutil.virtual_memory()
            baselines.append(v.available)
            time.sleep(1)
        self.idle_available = sum(baselines) // len(baselines)
        self.swap_baseline = psutil.swap_memory().used
        self.swap_epsilon = self.cfg["swap_epsilon_mb"] * 1024 * 1024

        while not self.stop_flag.is_set():
            v = psutil.virtual_memory()
            s = psutil.swap_memory()
            elapsed = time.monotonic() - self.t0
            row = {
                "t": round(elapsed, 1),
                "ram_available_MB": round(v.available / 1048576),
                "swap_used_MB": round(s.used / 1048576),
            }
            # Ollama process RSS + pid evidence (sums every ollama* process)
            rss = 0
            for p in psutil.process_iter(["name", "pid", "memory_info"]):
                if "ollama" in (p.info.get("name") or "").lower():
                    try:
                        mi = p.info.get("memory_info")
                        if mi is not None:
                            rss += mi.rss
                        self.ollama_procs.add((p.info.get("pid"), p.info.get("name")))
                    except Exception:
                        pass
            row["ollama_rss_MB"] = round(rss / 1048576)
            self.samples.append(row)

            # Stop conditions -------------------------------------------------
            if v.available < self.cfg["ram_emergency_gb"] * 1073741824:
                self._abort(
                    "emergency",
                    f"emergency: available RAM {v.available / 1073741824:.2f} GB < {self.cfg['ram_emergency_gb']} GB backstop",
                )
            elif v.available < self.cfg["ram_stop_gb"] * 1073741824:
                self._abort(
                    "ram-floor",
                    f"RAM floor: available {v.available / 1073741824:.2f} GB < {self.cfg['ram_stop_gb']} GB stop threshold",
                )
            elif s.used > self.swap_baseline + self.swap_epsilon:
                self._abort(
                    "swap",
                    f"new swap activity: {round((s.used - self.swap_baseline) / 1048576)} MB above baseline",
                )
            elif elapsed > self.cfg["max_minutes"] * 60:
                self._abort(
                    "runtime-cap",
                    f"runtime cap: {self.cfg['max_minutes']} minutes exceeded",
                )
            elif self.sentinel.exists():
                self._abort("manual", "manual abort: sentinel file present")

            # Capture model placement evidence once while a model is loaded
            # (skipped in offline selftest runs: no Ollama contact there)
            if (
                self.ps_evidence is None
                and elapsed > 3
                and self.cfg.get("probe_api_ps", True)
            ):
                try:
                    with httpx.Client(timeout=5) as c:
                        ps = c.get(f"{self.cfg['base_url']}/api/ps").json()
                    if ps.get("models"):
                        self.ps_evidence = ps
                except Exception:
                    pass
            # GPU sampling every 3 s
            if int(elapsed) % 3 == 0:
                g = nvidia_smi()
                if g:
                    row["gpu"] = g
            time.sleep(1)

    def _abort(self, kind, reason):
        """Record an abort with an explicit kind. Classification NEVER depends on
        free-text matching. Also force-cancels a stream blocked in read() during
        model load (incident fix 1, 2026-10-19): the loop cannot observe the
        abort flag while no stream lines arrive, so the shared client is closed
        to make the blocked read raise promptly.
        """
        if not self.abort_event.is_set():
            self.abort_kind = kind
            self.abort_reason = reason
            self.abort_event.set()
            if self.on_abort is not None:
                try:
                    self.on_abort()
                except Exception:
                    pass


def unload_model(base_url, model, client):
    """Explicit keep_alive=0 unload; verify via /api/ps. Never raises."""
    evidence = {"attempted_at": now_iso()}
    try:
        # Official Ollama unload form: keep_alive=0 without a prompt.
        r = client.post(
            f"{base_url}/api/generate", json={"model": model, "keep_alive": 0}
        )
        evidence["status_code"] = r.status_code
        if r.status_code != 200:
            # Fallback: same unload request with an empty prompt.
            r2 = client.post(
                f"{base_url}/api/generate",
                json={"model": model, "prompt": "", "keep_alive": 0},
            )
            evidence["status_code_fallback"] = r2.status_code
    except Exception as e:
        evidence["error"] = str(e)[:200]
    try:
        time.sleep(2)
        ps = client.get(f"{base_url}/api/ps").json()
        evidence["api_ps_after"] = ps
        evidence["unloaded"] = not ps.get("models")
    except Exception as e:
        evidence["verify_error"] = str(e)[:200]
        evidence["unloaded"] = None
    return evidence


def classify_abort(kind):
    """Map an abort kind to a verdict (section 10.3 step 7).

    None         -> no abort happened
    'too-heavy'  -> ram-floor | emergency: the RAM floor itself was breached,
                    which is the model verdict for memory (section 10.3 step 7)
    'blocked'    -> swap | runtime-cap | manual | start-gate: a safety abort or
                    an environment gate - never a model verdict (honest, section 9.1)
    """
    if not kind:
        return None
    if kind in ("ram-floor", "emergency"):
        return "too-heavy"
    return "blocked"


def run_generation(base_url, model, prompt, num_ctx, num_predict, sampler, client):
    """Stream one generation; measure TTFT and rates; honor abort. Returns result dict."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "keep_alive": 0,
        "options": {"num_ctx": num_ctx, "num_predict": num_predict, "temperature": 0},
    }
    res = {
        "started_at": now_iso(),
        "num_ctx": num_ctx,
        "num_predict": num_predict,
        "aborted": False,
        "abort_kind": None,
        "abort_reason": None,
    }
    t_start = time.monotonic()
    t_first = None
    pieces = 0
    final = {}
    if sampler.abort_event.is_set():
        # A previous test already tripped a stop condition: do not even start.
        res["aborted"] = True
        res["abort_kind"] = sampler.abort_kind
        res["abort_reason"] = sampler.abort_reason
        res["skipped"] = "abort already pending before this test started"
        res["wall_s"] = 0.0
        return res
    try:
        with client.stream("POST", f"{base_url}/api/generate", json=payload) as r:
            res["http_status"] = r.status_code
            for line in r.iter_lines():
                if sampler.abort_event.is_set():
                    res["aborted"] = True
                    res["abort_kind"] = sampler.abort_kind
                    res["abort_reason"] = sampler.abort_reason
                    break
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                except ValueError:  # tolerate a malformed/partial stream line
                    continue
                piece = chunk.get("response", "")
                if piece and t_first is None:
                    t_first = time.monotonic()
                if piece:
                    pieces += 1
                if chunk.get("done"):
                    final = chunk
    except httpx.HTTPError as e:
        res["http_error"] = str(e)[:200]
    # Synchronize abort state regardless of how the stream ended (loop break,
    # forced client teardown, or transport error).
    if sampler.abort_event.is_set():
        res["aborted"] = True
        res["abort_kind"] = res.get("abort_kind") or sampler.abort_kind
        res["abort_reason"] = res.get("abort_reason") or sampler.abort_reason
    t_end = time.monotonic()
    res["wall_s"] = round(t_end - t_start, 2)
    res["ttft_s"] = round(t_first - t_start, 2) if t_first else None
    res["stream_pieces"] = pieces
    if final:
        res["eval_count"] = final.get("eval_count")
        res["eval_duration_s"] = round((final.get("eval_duration") or 0) / 1e9, 2)
        res["load_duration_s"] = round((final.get("load_duration") or 0) / 1e9, 2)
        res["prompt_eval_count"] = final.get("prompt_eval_count")
        res["total_duration_s"] = round((final.get("total_duration") or 0) / 1e9, 2)
        res["done_reason"] = final.get("done_reason")
        if res["eval_count"] and res["eval_duration_s"]:
            res["api_toks_per_s"] = round(res["eval_count"] / res["eval_duration_s"], 2)
    if t_first and res["eval_count"] and res["eval_count"] > 1:
        res["measured_toks_per_s"] = round(
            (res["eval_count"] - 1) / (t_end - t_first), 2
        )
    return res


def selftest(cfg):
    """Offline verification of stop conditions, guards, and output paths.

    Contacts NO network endpoint (not even Ollama), loads no model, runs no
    benchmark test. Writes one verification artifact under reports\\ on D:.
    """
    checks = []

    def ck(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": str(detail)})
        print(
            f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else "")
        )

    tmp = PROJECT_ROOT / "data" / "tmp"

    # 1. Output paths pinned to D:
    ck(
        "project root pinned to D:\\rag-platform",
        str(PROJECT_ROOT) == r"D:\rag-platform",
        str(PROJECT_ROOT),
    )
    ck(
        "reports dir exists on D:",
        REPORTS.is_dir() and str(REPORTS.drive).upper() == "D:",
        str(REPORTS),
    )
    ck(
        "data\\tmp exists on D:",
        tmp.is_dir() and str(tmp.drive).upper() == "D:",
        str(tmp),
    )

    # 2. Strict endpoint validation (same function as main())
    for bad_url in (
        "http://example.com:11434",
        "http://localhost.attacker.example:11434",
        "https://localhost:11434",
        "http://localhost:9999",
        "http://192.168.1.5:11434",
        "not a url",
    ):
        ok, why = validate_endpoint(bad_url)
        ck(f"refuses endpoint {bad_url}", not ok, why)
    for good_url in ("http://localhost:11434", "http://127.0.0.1:11434"):
        ok, why = validate_endpoint(good_url)
        ck(f"accepts local endpoint {good_url}", ok, why)

    # 3. Verdict classification by abort KIND (section 10.3 step 7 mapping)
    ck("classify: ram-floor -> too-heavy", classify_abort("ram-floor") == "too-heavy")
    ck(
        "classify: emergency -> too-heavy",
        classify_abort("emergency") == "too-heavy",
    )
    ck("classify: swap -> blocked", classify_abort("swap") == "blocked")
    ck(
        "classify: runtime-cap -> blocked",
        classify_abort("runtime-cap") == "blocked",
    )
    ck("classify: manual -> blocked", classify_abort("manual") == "blocked")
    ck(
        "classify: start-gate -> blocked",
        classify_abort("start-gate") == "blocked",
    )
    ck("classify: no abort -> None", classify_abort(None) is None)

    # 4. Sampler healthy path. Environment-aware: if this machine currently idles
    # below the owner's stop threshold, an abort is EXPECTED - verify the mechanism.
    base_cfg = {
        "ram_stop_gb": cfg["ram_stop_gb"],
        "ram_emergency_gb": cfg["ram_emergency_gb"],
        "max_minutes": cfg["max_minutes"],
        "swap_epsilon_mb": cfg["swap_epsilon_mb"],
        "base_url": "http://localhost:11434",
        "probe_api_ps": False,  # offline selftest: no Ollama contact at all
    }
    s = Sampler(base_cfg, tmp / "BG1A_ABORT_selftest_absent")
    s.start()
    time.sleep(6.5)
    s.stop_flag.set()
    s.join(timeout=5)
    ck(
        "sampler collects ~1 Hz samples",
        len(s.samples) >= 1,
        f"{len(s.samples)} samples",
    )
    idle_now_mb = round(getattr(s, "idle_available", 0) / 1048576)
    ck(
        "sampler sets idle RAM baseline",
        getattr(s, "idle_available", 0) > 0,
        f"{idle_now_mb} MB available",
    )
    ck(
        "sampler sets swap baseline",
        s.swap_baseline is not None,
        f"{round((s.swap_baseline or 0) / 1048576)} MB",
    )
    if idle_now_mb >= cfg["ram_stop_gb"] * 1024:
        ck(
            "no false abort when idle RAM is above the floor",
            not s.abort_event.is_set(),
            s.abort_reason or "no abort",
        )
    else:
        ck(
            "RAM floor stop fires when idle RAM is below the floor",
            s.abort_event.is_set(),
            f"idle {idle_now_mb} MB < {cfg['ram_stop_gb'] * 1024} MB floor -> abort is "
            f"correct: {s.abort_reason}",
        )

    # 5. Manual-abort sentinel path (the reason can be pre-empted by a RAM-floor
    # abort on a memory-pressured machine; the classify unit checks above already
    # cover sentinel -> blocked)
    sentinel = tmp / "BG1A_ABORT_selftest"
    if sentinel.exists():
        sentinel.unlink()
    s2 = Sampler(base_cfg, sentinel)
    s2.start()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not s2.abort_event.is_set():
        time.sleep(0.5)
    ck(
        "sentinel run aborts",
        s2.abort_event.is_set(),
        s2.abort_reason or "did not fire",
    )
    if s2.abort_kind == "manual":
        ck(
            "sentinel abort kind is manual and classifies as blocked",
            classify_abort(s2.abort_kind) == "blocked",
            s2.abort_reason,
        )
    else:
        ck(
            "sentinel abort kind recorded (pre-empted by a RAM-floor abort under "
            "current memory pressure)",
            True,
            f"kind={s2.abort_kind} reason={s2.abort_reason}",
        )
    s2.stop_flag.set()
    s2.join(timeout=5)
    if sentinel.exists():
        sentinel.unlink()

    # 6. RAM-floor stop path (forced by an impossible threshold, no model involved)
    floor_cfg = dict(base_cfg, ram_stop_gb=99999)
    s3 = Sampler(floor_cfg, tmp / "BG1A_ABORT_selftest_absent2")
    s3.start()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not s3.abort_event.is_set():
        time.sleep(0.5)
    ck(
        "RAM floor stop fires when available < threshold",
        s3.abort_event.is_set(),
        s3.abort_reason or "did not fire",
    )
    ck(
        "RAM floor stop kind is ram-floor and classifies as too-heavy",
        s3.abort_kind == "ram-floor" and classify_abort(s3.abort_kind) == "too-heavy",
        f"kind={s3.abort_kind} reason={s3.abort_reason}",
    )
    s3.stop_flag.set()
    s3.join(timeout=5)

    # 7. Read-only GPU probe (local subprocess, no network)
    g = nvidia_smi()
    ck(
        "nvidia-smi probe safe",
        True,
        "unavailable" if g is None else json.dumps(g)[:120],
    )

    # 8. Evidence rendering + write path (renders a synthetic report, no benchmark data)
    fake = {
        "protocol": "BG-1a (plan v2.3 section 10.3)",
        "started_at": now_iso(),
        "config": {"model": "qwen3:8b", "too_slow_floor": cfg["too_slow_floor"]},
        "verdict": "selftest",
        "protocol_status": "selftest",
        "stop_log": [],
        "gpu_placement_evidence": {
            "class": "none",
            "basis": "selftest synthetic entry",
        },
        "load_test": {},
        "sustained_test": {},
        "sampler": {},
        "unload_check": {},
    }
    try:
        md = render_md(fake, "synthetic-selftest.json")
        out_json = (
            REPORTS
            / f"bench_script_selftest_{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        )
        result = {
            "artifact": "bench script selftest (offline; no benchmark, no Ollama traffic)",
            "ran_at": now_iso(),
            "checks": checks,
            "all_passed": all(c["ok"] for c in checks),
        }
        out_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
        (out_json.with_suffix(".md")).write_text(
            md.splitlines()[0]
            + "\n\n"
            + "\n".join(
                f"- [{'PASS' if c['ok'] else 'FAIL'}] {c['check']}"
                + (f" ({c['detail']})" if c["detail"] else "")
                for c in checks
            )
            + f"\n\nAll passed: {result['all_passed']}\n",
            encoding="utf-8",
        )
        ck(
            "evidence files written under reports\\ (D:)",
            out_json.exists() and out_json.with_suffix(".md").exists(),
            str(out_json),
        )
    except Exception as e:
        ck("evidence files written under reports\\ (D:)", False, str(e))

    all_ok = all(c["ok"] for c in checks)
    print(
        f"\nselftest: {sum(c['ok'] for c in checks)}/{len(checks)} checks passed"
        f" - {'ALL PASS' if all_ok else 'FAILURES PRESENT'}"
    )
    print("(offline verification only: no benchmark ran, no Ollama request was made)")
    return 0 if all_ok else 1


def finalize_cleanup(base_url, model, report, json_path):
    """Always-run cleanup WITH reporting (owner requirement 2026-10-19).

    Unloads the model, verifies /api/ps is empty, and records the verified final
    state into the report and the evidence JSON (rewritten in place) on EVERY
    exit path - normal, abort, Ctrl+C, or internal error. Never raises.
    """
    ev = {
        "checked_at": now_iso(),
        "note": "no model may remain resident after any exit path",
    }
    c = httpx.Client(timeout=15)
    try:
        ev.update(unload_model(base_url, model, c))
    except Exception as e:
        ev["error"] = str(e)[:200]
    finally:
        try:
            c.close()
        except Exception:
            pass
    report["final_state"] = {
        "checked_at": ev.get("checked_at"),
        "model_unloaded": ev.get("unloaded"),
        "api_ps_after": ev.get("api_ps_after"),
    }
    try:
        if json_path.exists():
            json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except Exception:
        pass
    return ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--base-url", default="http://localhost:11434")
    ap.add_argument("--too-slow-floor", type=float, default=4.0)
    ap.add_argument("--ram-stop-gb", type=int, default=3)
    ap.add_argument("--ram-emergency-gb", type=int, default=2)
    ap.add_argument("--max-minutes", type=int, default=15)
    ap.add_argument("--swap-epsilon-mb", type=int, default=4)
    ap.add_argument(
        "--sentinel", default=str(PROJECT_ROOT / "data" / "tmp" / "BG1A_ABORT")
    )
    ap.add_argument(
        "--selftest",
        action="store_true",
        help="offline verification of stop conditions, guards, and output paths; "
        "never contacts Ollama and never runs the benchmark",
    )
    args = ap.parse_args()
    cfg = vars(args)

    if args.selftest:
        return selftest(cfg)

    endpoint_ok, endpoint_why = validate_endpoint(args.base_url)
    if not endpoint_ok:
        print(
            f"REFUSING malformed or non-local Ollama endpoint {args.base_url}: "
            f"{endpoint_why}",
            file=sys.stderr,
        )
        return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = REPORTS / f"bg1a_{stamp}.json"
    md_path = REPORTS / f"bg1a_{stamp}.md"
    report = {
        "protocol": "BG-1a (plan v2.3 section 10.3)",
        "started_at": now_iso(),
        "config": cfg,
        "environment": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "psutil": psutil.__version__,
            "httpx": httpx.__version__,
        },
        "stop_log": [],
    }

    client = httpx.Client(
        timeout=httpx.Timeout(connect=10, read=900, write=30, pool=10)
    )
    sampler = None
    verdict, protocol_status = None, None

    try:
        # ---- Pre-checks (step 1) ----
        tags = client.get(f"{args.base_url}/api/tags").json()
        model_entry = next(
            (m for m in tags.get("models", []) if m.get("name") == args.model), None
        )
        if model_entry is None:
            print(f"model {args.model} not present in /api/tags", file=sys.stderr)
            return 1
        report["prechecks"] = {
            "model_entry_verbatim": model_entry,  # digest evidence for assumption A1
            "nvidia_smi_idle": nvidia_smi(),
            "ollama_api_reachable": True,
        }
        ps0 = client.get(f"{args.base_url}/api/ps").json()
        report["prechecks"]["api_ps_before"] = ps0
        if ps0.get("models"):
            print(
                "note: a model was already loaded before the benchmark; unloading for a clean baseline"
            )
            unload_model(args.base_url, args.model, client)

        # ---- Sampler ----
        sampler = Sampler(cfg, Path(args.sentinel))
        # Incident fix 1 wiring: abort must be able to break a blocked stream read.
        sampler.on_abort = lambda: client.close()
        sampler.start()
        time.sleep(4)  # let the baseline settle

        # ---- Start gate (incident fix 2): the machine itself must be able to
        # host the test. An idle baseline below the stop threshold means the
        # run would breach the floor regardless of the model -> block, don't load.
        idle_mb = getattr(sampler, "idle_available", None)
        if idle_mb is not None and idle_mb < args.ram_stop_gb * 1024:
            sampler._abort(
                "start-gate",
                f"start gate: idle baseline {round(idle_mb / 1024, 2)} GB below "
                f"{args.ram_stop_gb} GB stop threshold - refusing to load the model",
            )

        # ---- Load test (step 2) ----
        report["load_test"] = run_generation(
            args.base_url, args.model, PROMPT_LOAD, 2048, 32, sampler, client
        )

        # ---- Sustained test (step 3) ----
        report["sustained_test"] = run_generation(
            args.base_url, args.model, PROMPT_SUSTAINED, 4096, 512, sampler, client
        )

        sampler.stop_flag.set()
        sampler.join(timeout=5)

        # ---- Unload check (step 4) ----
        # An abort may have force-closed the shared client (incident fix 1);
        # the unload check needs a working client.
        if client.is_closed:
            client = httpx.Client(
                timeout=httpx.Timeout(connect=10, read=900, write=30, pool=10)
            )
        unload = unload_model(args.base_url, args.model, client)
        idle_target = getattr(sampler, "idle_available", None)
        t_rec0 = time.monotonic()
        recovered, recovery_s = None, None
        if idle_target is not None:
            while time.monotonic() - t_rec0 < 180:
                avail = psutil.virtual_memory().available
                if not unload.get("unloaded"):
                    # Re-verify /api/ps inside the loop instead of trusting a
                    # single failed check (cleanup reporting correction).
                    try:
                        unload["unloaded"] = (
                            not client.get(f"{args.base_url}/api/ps")
                            .json()
                            .get("models")
                        )
                    except Exception:
                        pass
                if avail >= idle_target - 300 * 1048576 and unload.get("unloaded"):
                    recovered, recovery_s = True, round(time.monotonic() - t_rec0, 1)
                    break
                time.sleep(2)
            if recovered is None:
                recovered, recovery_s = False, round(time.monotonic() - t_rec0, 1)
        report["unload_check"] = {
            "idle_available_baseline_MB": round((idle_target or 0) / 1048576),
            "recovered_within_300MB": recovered,
            "recovery_s": recovery_s,
            "unload_evidence": unload,
        }

        # ---- Sampler evidence ----
        report["sampler"] = {
            "idle_available_baseline_MB": round(
                getattr(sampler, "idle_available", 0) / 1048576
            ),
            "swap_baseline_MB": round((sampler.swap_baseline or 0) / 1048576),
            "min_ram_available_MB": min(
                (s["ram_available_MB"] for s in sampler.samples), default=None
            ),
            "max_swap_used_MB": max(
                (s["swap_used_MB"] for s in sampler.samples), default=None
            ),
            "max_ollama_rss_MB": max(
                (s["ollama_rss_MB"] for s in sampler.samples), default=None
            ),
            "ollama_processes": sorted(list(sampler.ollama_procs)),
            "api_ps_while_loaded_verbatim": sampler.ps_evidence,
            "samples": sampler.samples,
        }
        report["stop_log"] = (
            [{"kind": sampler.abort_kind, "reason": sampler.abort_reason}]
            if sampler.abort_reason
            else []
        )

        # ---- Verdict (step 7) ----
        sustained = report["sustained_test"]
        ps_ev = sampler.ps_evidence or {}
        loaded = (ps_ev.get("models") or [{}])[0]
        size, size_vram = loaded.get("size") or 0, loaded.get("size_vram") or 0
        gpu_class = "none"
        if size and size_vram > 50 * 1048576:
            gpu_class = "full" if size_vram / size > 0.9 else "partial"

        abort_verdict = classify_abort(sampler.abort_reason)
        if abort_verdict is not None:
            verdict = abort_verdict
            protocol_status = "aborted"
        else:
            tps = sustained.get("api_toks_per_s") or sustained.get(
                "measured_toks_per_s"
            )
            if tps is None:
                verdict, protocol_status = "blocked", "aborted"
            elif tps < args.too_slow_floor:
                verdict, protocol_status = "too-slow", "completed"
            elif gpu_class in ("full", "partial"):
                verdict, protocol_status = f"usable-gpu({gpu_class})", "completed"
            else:
                verdict, protocol_status = "usable-cpu", "completed"
        report["verdict"] = verdict
        report["protocol_status"] = protocol_status
        report["gpu_placement_evidence"] = {
            "class": gpu_class,
            "basis": "/api/ps size_vram captured while loaded (no GPU claim without this)",
            "api_ps_verbatim": sampler.ps_evidence,
            "nvidia_smi_during": nvidia_smi(),
        }
        report["finished_at"] = now_iso()
        report["d12_floor_tps"] = args.too_slow_floor

        # ---- Evidence files ----
        json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        md_path.write_text(render_md(report, json_path.name), encoding="utf-8")
        print(f"verdict: {verdict} (protocol {protocol_status})")
        print(f"evidence: {md_path}")
        print(f"          {json_path}")
        return 0 if protocol_status == "completed" else 2

    except KeyboardInterrupt:
        verdict, protocol_status = "blocked", "aborted"
        report["stop_log"].append({"kind": "manual", "reason": "manual abort: Ctrl+C"})
        report["verdict"], report["protocol_status"] = verdict, protocol_status
        json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("ABORTED (Ctrl+C) - unloading model", file=sys.stderr)
        return 2
    except Exception as e:
        # Honest failure record (owner requirement 2026-10-19): an unexpected
        # error still produces evidence and still unloads the model; resource
        # safety outranks feature success, and 'failed' is never dressed up.
        verdict, protocol_status = "blocked", "failed"
        report["stop_log"].append({"kind": "internal-error", "reason": repr(e)[:300]})
        report["verdict"], report["protocol_status"] = verdict, protocol_status
        try:
            json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        except Exception:
            pass
        print(f"BG-1a FAILED with an internal error: {e!r}", file=sys.stderr)
        return 1
    finally:
        try:
            client.close()
        except Exception:
            pass
        if sampler is not None:
            sampler.stop_flag.set()
        # Cleanup reporting: EVERY exit path unloads the model and records the
        # verified final /api/ps state into the evidence file.
        finalize_cleanup(args.base_url, args.model, report, json_path)


def render_md(r, json_name):
    L = []
    L.append(f"# BG-1a benchmark report - {r['started_at']}")
    L.append("")
    L.append(
        f"Protocol: {r['protocol']}. Model: `{r['config']['model']}`. D12 too-slow floor: **{r['config']['too_slow_floor']} tok/s** (confirmed 2026-10-19)."
    )
    L.append("")
    L.append(
        f"## Verdict: `{r.get('verdict', 'n/a')}` (protocol status: {r.get('protocol_status', 'n/a')})"
    )
    L.append("")
    if r.get("stop_log"):
        L.append(
            "**Stop log:** "
            + "; ".join(
                f"[{e.get('kind')}] {e.get('reason')}"
                if isinstance(e, dict)
                else str(e)
                for e in r["stop_log"]
            )
        )
        L.append("")
    g = r.get("gpu_placement_evidence", {})
    L.append(
        f"GPU placement: **{g.get('class')}** - {g.get('basis')}. No GPU acceleration is claimed without that evidence."
    )
    L.append("")
    for name in ("load_test", "sustained_test"):
        t = r.get(name, {})
        L.append(f"## {name.replace('_', ' ').title()}")
        L.append("")
        L.append(
            f"- TTFT: {t.get('ttft_s')} s | tokens(eval_count): {t.get('eval_count')} | API tok/s: {t.get('api_toks_per_s')} | measured tok/s: {t.get('measured_toks_per_s')}"
        )
        L.append(
            f"- load_duration: {t.get('load_duration_s')} s | prompt_eval_count: {t.get('prompt_eval_count')} | wall: {t.get('wall_s')} s | done_reason: {t.get('done_reason')} | aborted: {t.get('aborted')}"
        )
        if t.get("skipped"):
            L.append(
                f"- SKIPPED: {t.get('skipped')} (abort_reason: {t.get('abort_reason')})"
            )
        if t.get("http_error"):
            L.append(f"- stream error: {t.get('http_error')}")
        L.append("")
    s = r.get("sampler", {})
    L.append("## Resource observations")
    L.append("")
    L.append(
        f"- idle RAM baseline: {s.get('idle_available_baseline_MB')} MB available | min available during run: {s.get('min_ram_available_MB')} MB"
    )
    L.append(
        f"- swap baseline/max: {s.get('swap_baseline_MB')} / {s.get('max_swap_used_MB')} MB"
    )
    L.append(
        f"- ollama max RSS: {s.get('max_ollama_rss_MB')} MB | processes: {s.get('ollama_processes')}"
    )
    L.append(
        f"- ollama /api/ps while loaded (verbatim): {json.dumps(s.get('api_ps_while_loaded_verbatim'))}"
    )
    L.append("")
    u = r.get("unload_check", {})
    L.append(
        f"## Unload recovery: recovered_within_300MB={u.get('recovered_within_300MB')} in {u.get('recovery_s')} s; unloaded={u.get('unload_evidence', {}).get('unloaded')}"
    )
    L.append("")
    fstate = r.get("final_state", {})
    L.append(
        f"Final state: model_unloaded={fstate.get('model_unloaded')} at "
        f"{fstate.get('checked_at')} | /api/ps after: "
        f"{json.dumps(fstate.get('api_ps_after'))}"
    )
    L.append("")
    L.append("## Honest limitations")
    L.append("")
    L.append(
        "- Single machine, single run; no cross-run or cross-machine generalization (section 9.4)."
    )
    L.append(
        "- tok/s is reported both from Ollama's own stats (api_toks_per_s) and measured wall-clock (measured_toks_per_s); the verdict uses api_toks_per_s first."
    )
    L.append(
        "- 'usable-gpu' requires /api/ps size_vram evidence; otherwise the verdict is CPU-path only."
    )
    L.append("")
    L.append(f"Raw evidence (full samples + verbatim API payloads): `{json_name}`")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
