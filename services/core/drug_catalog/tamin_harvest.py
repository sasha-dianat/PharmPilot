"""تأمین اجتماعی formulary harvester — supervises the standalone
`tamin/harvest_tamin_formulary.py` script as a background subprocess.

The script replays DevExpress ASPxGridView callbacks page by page, which is
slow and network-fragile, so it is run out-of-process with --resume: a killed
or failed run continues from the existing CSV/JSON instead of restarting.
Progress is parsed from its stderr ("harvested page X/Y: N rows").

Single-flight like the other harvesters; no LLM, no DB writes — it only
produces the CSV/JSON that the normal coverage-source flow then ingests.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCRIPT_DIR = Path("tamin")
SCRIPT = "harvest_tamin_formulary.py"
DEFAULT_INPUT = str(Path.home() / "Downloads" / "معاونت درمان سازمان تامین اجتماعی.html")
_PROGRESS_RE = re.compile(r"harvested page (\d+)/(\d+):\s*(\d+) rows")


@dataclass
class TaminHarvestState:
    running: bool = False
    phase: str = "idle"          # idle | harvesting | done | failed | cancelled
    page: int = 0
    page_count: int = 0
    rows: int = 0
    returncode: int | None = None
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    log: list = field(default_factory=list)      # tail of stderr lines

    def snapshot(self) -> dict:
        d = asdict(self)
        d["elapsed_sec"] = round((self.finished_at or time.time()) - self.started_at, 1) \
            if self.started_at else 0.0
        d["pct"] = round(100 * self.page / self.page_count, 1) if self.page_count else 0.0
        return d


_STATE = TaminHarvestState()
_PROC: asyncio.subprocess.Process | None = None


def status() -> dict:
    return _STATE.snapshot()


def build_command(*, input_html: str | None = None, delay: float = 1,
                  timeout: int = 120, max_retries: int = 20, retry_delay: int = 5,
                  csv_out: str = "tamin_formulary_full.csv",
                  json_out: str = "tamin_formulary_full.json",
                  online: bool = True, resume: bool = True,
                  pages: str | None = None) -> list[str]:
    """The exact CLI the owner runs by hand, as an argv list (no shell)."""
    py = os.environ.get("PHARMPILOT_PYTHON") or sys.executable
    cmd = [py, SCRIPT]
    if online:
        cmd.append("--online")
    if resume:
        cmd.append("--resume")
    cmd += ["--input", input_html or DEFAULT_INPUT,
            "--delay", str(delay), "--timeout", str(timeout),
            "--max-retries", str(max_retries), "--retry-delay", str(retry_delay),
            "--csv", csv_out, "--json", json_out]
    if pages:
        cmd += ["--pages", pages]
    return cmd


def _push_log(line: str) -> None:
    _STATE.log.append(line[:300])
    if len(_STATE.log) > 60:
        _STATE.log = _STATE.log[-60:]


async def _pump(stream) -> None:
    """Parse progress from the script's stderr while keeping a log tail."""
    while True:
        raw = await stream.readline()
        if not raw:
            return
        line = raw.decode("utf-8", "replace").rstrip()
        if not line:
            continue
        _push_log(line)
        m = _PROGRESS_RE.search(line)
        if m:
            _STATE.page, _STATE.page_count = int(m.group(1)), int(m.group(2))
            _STATE.rows += int(m.group(3))


async def _run(cmd: list[str], cwd: Path) -> None:
    global _PROC
    try:
        _PROC = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(cwd),
            stdout=asyncio.subprocess.DEVNULL,       # payload goes to the files
            stderr=asyncio.subprocess.PIPE)
        await _pump(_PROC.stderr)
        rc = await _PROC.wait()
        _STATE.returncode = rc
        if _STATE.phase == "cancelled":
            pass
        elif rc == 0:
            _STATE.phase = "done"
        else:
            _STATE.phase = "failed"
            _STATE.error = f"خروج با کد {rc}" + (f" — {_STATE.log[-1]}" if _STATE.log else "")
    except Exception as e:
        _STATE.phase = "failed"
        _STATE.error = f"{type(e).__name__}: {e}"
    finally:
        _STATE.running = False
        _STATE.finished_at = time.time()
        _PROC = None


def start(**kwargs) -> dict:
    """Launch the harvest in the background. Raises if one is already running
    or the script/input is missing."""
    global _STATE
    if _STATE.running:
        raise RuntimeError("برداشت تأمین هم‌اکنون در حال اجراست")
    cwd = Path.cwd() / SCRIPT_DIR
    if not (cwd / SCRIPT).exists():
        raise RuntimeError(f"اسکریپت یافت نشد: {cwd / SCRIPT}")
    input_html = kwargs.get("input_html") or DEFAULT_INPUT
    if not Path(input_html).exists():
        raise RuntimeError(f"فایل ورودی HTML یافت نشد: {input_html}")

    cmd = build_command(**kwargs)
    _STATE = TaminHarvestState(running=True, phase="harvesting", started_at=time.time())
    _push_log("$ " + " ".join(cmd))
    asyncio.create_task(_run(cmd, cwd))
    return _STATE.snapshot()


def stop() -> dict:
    """Terminate the running harvest. --resume means the next run continues
    from the rows already written."""
    if not _STATE.running or _PROC is None:
        raise RuntimeError("برداشتی در جریان نیست")
    _STATE.phase = "cancelled"
    try:
        _PROC.terminate()
    except ProcessLookupError:
        pass
    return _STATE.snapshot()


def output_info(csv_out: str = "tamin_formulary_full.csv",
                json_out: str = "tamin_formulary_full.json") -> dict:
    """Size/mtime/row-count of the produced files, for the panel."""
    cwd = Path.cwd() / SCRIPT_DIR
    out: dict = {}
    for key, name in (("csv", csv_out), ("json", json_out)):
        p = cwd / name
        if p.exists():
            st = p.stat()
            info = {"path": str(p), "bytes": st.st_size, "mtime": st.st_mtime}
            if key == "csv":
                try:
                    with p.open(encoding="utf-8-sig") as fh:
                        info["rows"] = max(0, sum(1 for _ in fh) - 1)
                except OSError:
                    pass
            out[key] = info
    return out
