"""Shuffle-rename media in a trusted local Windows folder.

Use: python shuffles.py <absolute-folder> [--quiet]
     [--recovery=abort|continue|rollback|discard]

Pass 1 stages each supported file at _temp_shuffles_<32hex><extension>.
Pass 2 assigns folder-name-N.extension, with separate extension counters.
Windows rename refuses occupied destinations; media contents are never rewritten.
A version-2 journal is atomically persisted before renaming. Recovery identifies
files by volume/file ID, size and modification time and stages the entire set
before restoring either original or final names. Completed/discarded journals
are archived in %TEMP%/.shuffles-archive, not permanently deleted.

The per-folder lock covers GUI decisions, journal loading and all rename phases.
Quiet mode emits JSON and exit code 0/1 without dialogs. Hidden supported media
is included and unhidden after successful final renaming. See README.md for
supported folders, recovery limits, privacy and installation instructions.
"""

import os
import sys
import json
import uuid
import ctypes
import pathlib
import hashlib
import datetime
import re
import tempfile
import stat

# ── Configuration ────────────────────────────────────────────────────────────

ELIGIBLE_EXTENSIONS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
    ".webp", ".heic", ".heif", ".avif",
    ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v",
})

TEMP_PREFIX   = "_temp_shuffles_"
LOCK_FILE     = ".shuffles.lock"
LOG_FILE      = ".shuffles_last.log"
VERBOSE_LOG_THRESHOLD = 300  # per-file log lines only below this file count


# ── Path-keyed filenames in %TEMP% ───────────────────────────────────────────

def _dir_hash(path: pathlib.Path) -> str:
    canonical = os.path.normcase(str(path.resolve()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def manifest_path(target_dir: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(tempfile.gettempdir()) / f"shuffles_manifest_{_dir_hash(target_dir)}.json"


def _validate_target(target_dir: pathlib.Path) -> pathlib.Path:
    if os.name != "nt":
        raise RuntimeError("Shuffles requires Windows rename semantics.")
    if not target_dir.is_absolute() or str(target_dir).startswith("\\\\"):
        raise RuntimeError("Use a fully qualified local Windows folder.")
    if not target_dir.is_dir() or target_dir == pathlib.Path(target_dir.anchor):
        raise RuntimeError("Choose an existing folder, not a drive root.")
    for ancestor in (target_dir, *target_dir.parents):
        state = ancestor.lstat()
        if state.st_file_attributes & (0x400 | 0x1000 | 0x40000 | 0x400000):
            raise RuntimeError("Reparse, offline or recall-on-access folders are unsupported.")
    resolved = target_dir.resolve()
    protected = [pathlib.Path(value).resolve() for key in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")
                 if (value := os.environ.get(key))]
    if resolved == pathlib.Path.home().resolve() or any(resolved == root or root in resolved.parents for root in protected):
        raise RuntimeError("Profile and system folders are unsupported.")
    if any(part.casefold() in {"$recycle.bin", "system volume information"} for part in resolved.parts):
        raise RuntimeError("Windows recovery/system metadata folders are unsupported.")
    return resolved


def _file_identity(path: pathlib.Path) -> list[int]:
    state = path.lstat()
    if (not stat.S_ISREG(state.st_mode) or state.st_nlink != 1 or
            getattr(state, "st_file_attributes", 0) & (0x400 | 0x1000 | 0x40000 | 0x400000)):
        raise RuntimeError("Unsupported linked, nonlocal or nonregular file.")
    if not state.st_ino:
        raise RuntimeError("Stable file identity is unavailable.")
    return [state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns]


def _matches(path: pathlib.Path, entry: dict) -> bool:
    try:
        return _file_identity(path) == entry["identity"]
    except (OSError, RuntimeError):
        return False


def _leaf_name(name: object) -> bool:
    if not isinstance(name, str) or not name or name in {".", ".."} or name[-1] in " .":
        return False
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        return False
    stem = name.split(".", 1)[0].upper()
    return stem not in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def validate_manifest(target_dir: pathlib.Path, data: object) -> dict:
    if not isinstance(data, dict) or set(data) != {"version", "target_dir", "created", "entries"}:
        raise RuntimeError("Invalid or legacy recovery format; preserve it and recover with the original version.")
    if data["version"] != 2 or data["target_dir"] != os.path.normcase(str(target_dir.resolve())):
        raise RuntimeError("Recovery data belongs to a different folder or version.")
    entries = data["entries"]
    if not isinstance(entries, list) or not entries or len(entries) > 100000:
        raise RuntimeError("Invalid recovery entry list.")
    seen = {key: set() for key in ("original", "temp", "final")}
    identities = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"original", "temp", "final", "identity", "was_hidden"}:
            raise RuntimeError("Invalid recovery entry schema.")
        for key in seen:
            name = entry[key]
            if not _leaf_name(name) or name.casefold() in seen[key]:
                raise RuntimeError("Recovery names must be unique plain filenames.")
            seen[key].add(name.casefold())
        identity = entry["identity"]
        if (not isinstance(identity, list) or len(identity) != 4 or
                any(type(x) is not int or x < 0 for x in identity) or not identity[1] or
                tuple(identity) in identities or type(entry["was_hidden"]) is not bool):
            raise RuntimeError("Invalid or duplicate recovery file identity.")
        identities.add(tuple(identity))
        ext = pathlib.Path(entry["original"]).suffix.lower()
        if ext not in ELIGIBLE_EXTENSIONS or pathlib.Path(entry["final"]).suffix.lower() != ext:
            raise RuntimeError("Recovery file extensions do not match.")
        if not re.fullmatch(re.escape(TEMP_PREFIX) + r"[a-f0-9]{32}" + re.escape(ext), entry["temp"]):
            raise RuntimeError("Invalid recovery temporary filename.")
    if seen["temp"] & (seen["original"] | seen["final"]):
        raise RuntimeError("Temporary recovery names overlap other names.")
    return data


def _checked_rename(source: pathlib.Path, destination: pathlib.Path, entry: dict) -> None:
    _validate_target(source.parent)
    if source.parent != destination.parent or not _matches(source, entry):
        raise RuntimeError("Source identity changed; rename refused.")
    # Windows os.rename never replaces an existing destination. Do not use
    # os.replace here: it is reserved for our own atomic manifest write.
    source.rename(destination)
    if not _matches(destination, entry):
        raise RuntimeError("File changed during rename; preserve recovery data.")


def _locate(target_dir: pathlib.Path, entry: dict) -> pathlib.Path:
    matches = {target_dir / entry[key] for key in ("temp", "final", "original")
               if _matches(target_dir / entry[key], entry)}
    if len(matches) != 1:
        raise RuntimeError("A recovery file is missing or has changed; no substitute is accepted.")
    return matches.pop()


def _apply_recovery(target_dir: pathlib.Path, manifest: dict, destination_key: str) -> dict:
    validate_manifest(target_dir, manifest)
    entries = manifest["entries"]
    result = {"restored": 0, "failed": []}
    try:
        locations = [_locate(target_dir, entry) for entry in entries]
        owned = {os.path.normcase(str(path)) for path in locations}
        for entry in entries:
            for key in ("temp", destination_key):
                path = target_dir / entry[key]
                if os.path.lexists(path) and os.path.normcase(str(path)) not in owned:
                    raise RuntimeError("A recovery destination is occupied by an unrelated entry.")
        write_log(target_dir, [f"Recovery started: {destination_key}"])
        # Restage every member first. This also supports original/final name
        # cycles and interruptions during either pass without guessing identity.
        for entry, current in zip(entries, locations):
            temp = target_dir / entry["temp"]
            if current != temp:
                _checked_rename(current, temp, entry)
        for entry in entries:
            temp = target_dir / entry["temp"]
            destination = target_dir / entry[destination_key]
            _checked_rename(temp, destination, entry)
            if destination_key == "original" and entry["was_hidden"]:
                set_hidden(destination)
            elif destination_key == "final":
                clear_hidden(destination)
            result["restored"] += 1
        write_log(target_dir, [f"Recovery completed: {destination_key}; files: {result['restored']}"])
    except (OSError, RuntimeError) as error:
        result["failed"].append(str(error))
    return result



# ── Windows helpers ──────────────────────────────────────────────────────────

def msgbox(title: str, message: str, style: int = 0x40) -> int:
    """
    Native Windows MessageBox.  Returns the button code.
    style flags: 0x00=OK, 0x01=OKCancel, 0x03=YesNoCancel, 0x04=YesNo
    icon flags:  0x10=Error, 0x30=Warning, 0x40=Information
    """
    return ctypes.windll.user32.MessageBoxW(0, message, title, style)


def set_hidden(path: pathlib.Path) -> None:
    """
    Mark a file hidden on Windows by OR-ing the hidden bit (0x02) into the
    existing attributes rather than overwriting them.
    """
    try:
        existing = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if existing == -1:
            existing = 0
        ctypes.windll.kernel32.SetFileAttributesW(str(path), existing | 0x02)
    except Exception:
        pass  # non-fatal


def clear_hidden(path: pathlib.Path) -> bool:
    """Remove the Windows hidden attribute. Returns True if the bit was cleared."""
    try:
        existing = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if existing == -1 or not (existing & 0x02):
            return False
        ctypes.windll.kernel32.SetFileAttributesW(str(path), existing & ~0x02)
        return True
    except Exception:
        return False


def is_hidden(path: pathlib.Path) -> bool:
    attrs = get_file_attrs(path)
    return attrs != -1 and bool(attrs & 0x02)


def get_file_attrs(path: pathlib.Path) -> int:
    """Return Windows file attributes, or -1 on error."""
    try:
        return ctypes.windll.kernel32.GetFileAttributesW(str(path))
    except Exception:
        return -1


# ── Sanitize folder name ─────────────────────────────────────────────────────

def sanitize_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    cleaned = re.sub(r'\s+', '-', cleaned)
    cleaned = cleaned.rstrip(' .')
    return cleaned.lower()[:120].rstrip(" .")


# ── Logging ──────────────────────────────────────────────────────────────────

def write_log(target_dir: pathlib.Path, lines: list[str]) -> None:
    log_path = target_dir / LOG_FILE
    try:
        if os.path.lexists(log_path):
            _file_identity(log_path)  # Never follow a link or write an unsupported entry.
            # Opening existing hidden files with CREATE_ALWAYS can be denied on
            # Windows. r+ preserves attributes and uses OPEN_EXISTING instead.
            with log_path.open("r+", encoding="utf-8") as stream:
                stream.seek(0)
                stream.write("\n".join(lines))
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
        else:
            with log_path.open("x", encoding="utf-8") as stream:
                stream.write("\n".join(lines))
                stream.flush()
                os.fsync(stream.fileno())
        set_hidden(log_path)
    except (OSError, RuntimeError) as error:
        raise RuntimeError("Cannot maintain the local operation log; recovery data preserved.") from error


# ── Lock file ────────────────────────────────────────────────────────────────

def acquire_lock(target_dir: pathlib.Path) -> pathlib.Path:
    lock = target_dir / LOCK_FILE
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise RuntimeError("Another shuffle is running or a previous run left a lock. Confirm no process is active before archiving the stale lock.") from error
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(f"pid={os.getpid()} started={datetime.datetime.now().isoformat()}")
        stream.flush()
        os.fsync(stream.fileno())
    set_hidden(lock)
    return lock


def release_lock(lock: pathlib.Path) -> None:
    try:
        lock.unlink(missing_ok=True)
    except Exception:
        pass


# ── Manifest ─────────────────────────────────────────────────────────────────

def write_manifest(target_dir: pathlib.Path, entries: list[dict]) -> pathlib.Path:
    mp = manifest_path(target_dir)
    if os.path.lexists(mp):
        raise RuntimeError("Existing recovery data must be handled before a new run.")
    payload = {"version": 2, "target_dir": os.path.normcase(str(target_dir.resolve())),
               "created": datetime.datetime.now().isoformat(), "entries": entries}
    validate_manifest(target_dir, payload)
    mp.parent.mkdir(parents=True, exist_ok=True)
    staging = mp.with_name(mp.name + "." + uuid.uuid4().hex + ".tmp")
    with staging.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(staging, mp)
    return mp


def load_manifest(target_dir: pathlib.Path) -> dict | None:
    mp = manifest_path(target_dir)
    if not os.path.lexists(mp):
        return None
    _file_identity(mp)
    if mp.stat().st_size > 16 * 1024 * 1024:
        raise RuntimeError("Unsupported recovery file; preserve it for inspection.")
    try:
        data = json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as error:
        raise RuntimeError("Unreadable recovery data; preserved without starting a new run.") from error
    return validate_manifest(target_dir, data)


def delete_manifest(target_dir: pathlib.Path) -> None:
    # Preserve completed/discarded mappings instead of permanently deleting them.
    mp = manifest_path(target_dir)
    if not os.path.lexists(mp):
        return
    archive = mp.parent / ".shuffles-archive"
    archive.mkdir(exist_ok=True)
    mp.rename(archive / (mp.stem + "_" + uuid.uuid4().hex + ".json"))


# ── Recovery (GUI) ───────────────────────────────────────────────────────────

def offer_recovery_gui(target_dir: pathlib.Path, manifest: dict) -> str:
    """Choose recovery while run_core holds the per-folder lock."""
    choice = msgbox(
        "Shuffles - Interrupted operation",
        f"An interrupted operation from {manifest['created']} needs attention.\n\n"
        "[Yes] Restore original filenames\n"
        "[No] Finish the planned filenames\n"
        "[Cancel] Leave media unchanged\n\n"
        "Missing or changed files stop recovery. No substitute is accepted.",
        0x03 | 0x30,
    )
    return {6: "rollback", 7: "continue"}.get(choice, "abort")


def do_rollback(target_dir: pathlib.Path, manifest: dict) -> dict:
    return _apply_recovery(target_dir, manifest, "original")


# ── File collection ──────────────────────────────────────────────────────────

def collect_eligible(target_dir: pathlib.Path) -> list[pathlib.Path]:
    files = []
    for path in target_dir.iterdir():
        if path.name.startswith(TEMP_PREFIX) or path.suffix.lower() not in ELIGIBLE_EXTENSIONS:
            continue
        try:
            _file_identity(path)
        except (OSError, RuntimeError):
            continue
        files.append(path)
    return files


def list_hidden_eligible(target_dir: pathlib.Path) -> list[pathlib.Path]:
    """Return hidden media files that would otherwise be eligible for shuffling."""
    hidden: list[pathlib.Path] = []
    for p in target_dir.iterdir():
        if p.is_symlink() or not p.is_file():
            continue
        if p.name.startswith(TEMP_PREFIX):
            continue
        if p.suffix.lower() not in ELIGIBLE_EXTENSIONS:
            continue
        if is_hidden(p):
            hidden.append(p)
    return hidden


def count_hidden_eligible(target_dir: pathlib.Path) -> int:
    return len(list_hidden_eligible(target_dir))


def unhide_eligible_media(target_dir: pathlib.Path) -> list[str]:
    """
    Clear the hidden attribute on eligible media so they are included in shuffles.
    Returns the filenames that were unhidden.
    """
    unhidden: list[str] = []
    for p in list_hidden_eligible(target_dir):
        if clear_hidden(p):
            unhidden.append(p.name)
    return unhidden


def has_leftover_temps(target_dir: pathlib.Path) -> bool:
    """True if any _temp_shuffles_* files remain in the target directory."""
    for p in target_dir.iterdir():
        if p.is_file() and p.name.startswith(TEMP_PREFIX):
            return True
    return False


def count_leftover_temps(target_dir: pathlib.Path) -> int:
    return sum(
        1 for p in target_dir.iterdir()
        if p.is_file() and p.name.startswith(TEMP_PREFIX)
    )


def _name_key(name: str) -> str:
    """Case-insensitive lookup key (Windows filesystem is case-insensitive)."""
    return name.casefold()


def reserved_names(target_dir: pathlib.Path, shuffling: set[str]) -> set[str]:
    shuffling_keys = {_name_key(name) for name in shuffling}
    # Directories, symlinks and other unsupported entries reserve names too.
    return {_name_key(path.name) for path in target_dir.iterdir()
            if _name_key(path.name) not in shuffling_keys}


def occupied_non_temp_names(target_dir: pathlib.Path) -> set[str]:
    """All non-temp filenames currently present (used during Pass 2)."""
    return reserved_names(target_dir, set())


def path_name_exists(target_dir: pathlib.Path, name: str) -> bool:
    """True if `name` exists in target_dir (case-insensitive on Windows)."""
    return (target_dir / name).exists()


def _final_name_pattern(folder_name: str, ext: str) -> re.Pattern[str]:
    return re.compile(
        rf"^{re.escape(folder_name)}-(\d+){re.escape(ext)}$",
        re.IGNORECASE,
    )


def allocate_final_name(
    folder_name: str,
    ext: str,
    occupied: set[str],
    prefer: str | None = None,
) -> str:
    """
    Pick a collision-free final name, preferring `prefer` when it is free.
    The chosen name is added to `occupied` before returning.
    """
    def is_free(candidate: str) -> bool:
        return _name_key(candidate) not in occupied

    if prefer and is_free(prefer):
        occupied.add(_name_key(prefer))
        return prefer

    start_n = 1
    if prefer:
        m = _final_name_pattern(folder_name, ext).match(prefer)
        if m:
            start_n = int(m.group(1))

    n = start_n
    while True:
        candidate = f"{folder_name}-{n}{ext}"
        n += 1
        if is_free(candidate):
            occupied.add(_name_key(candidate))
            return candidate


def classify_manifest_entries(target_dir: pathlib.Path, entries: list[dict]) -> tuple[list[dict], int, int, int]:
    pending, finalized, blocked, lost = [], 0, 0, 0
    for entry in entries:
        try:
            path = _locate(target_dir, entry)
        except RuntimeError:
            lost += 1
            continue
        if path.name == entry["final"]:
            finalized += 1
        else:
            pending.append(entry)
        final = target_dir / entry["final"]
        if os.path.lexists(final) and not _matches(final, entry):
            blocked += 1
    return pending, finalized, blocked, lost


def recovery_is_stale(pending: int, lost: int, blocked: int) -> bool:
    """True when the manifest no longer describes the files on disk."""
    return pending == 0 and (lost > 0 or blocked > 0)


def complete_recovery_pass2(target_dir: pathlib.Path, entries: list[dict], folder_name: str) -> dict:
    manifest = load_manifest(target_dir)
    if manifest is None or manifest["entries"] != entries:
        raise RuntimeError("Recovery state changed; operation refused.")
    pending, finalized, blocked, lost = classify_manifest_entries(target_dir, entries)
    applied = _apply_recovery(target_dir, manifest, "final")
    result = {"pending": len(pending), "finalized": finalized, "blocked": blocked,
              "lost": lost, "renamed": applied["restored"], "errors": applied["failed"],
              "status": "recovery_partial" if applied["failed"] else "recovery_complete"}
    if not applied["failed"]:
        delete_manifest(target_dir)
    return result


# ── Pre-flight checks ─────────────────────────────────────────────────────────

def preflight(target_dir: pathlib.Path, folder_name: str) -> None:
    if not folder_name:
        raise RuntimeError(
            f"The target folder's name ('{target_dir.name}') produces an empty "
            f"string after sanitization. Rename the folder to something "
            f"meaningful before organizing it."
        )
    probe = target_dir / f".shuffles_writecheck_{uuid.uuid4().hex[:8]}"
    try:
        probe.touch()
        probe.unlink()
    except OSError as ex:
        raise RuntimeError(
            f"The target directory is not writable:\n  {target_dir}\n\n"
            f"OS error: {ex}\n\nCheck folder permissions and try again."
        ) from ex


# ── Core shuffle (logic only, no GUI) ────────────────────────────────────────

def shuffle_rename(
    target_dir: pathlib.Path,
    files: list[pathlib.Path],
    folder_name: str,
) -> dict:
    """
    Execute the two-pass rename. Returns a dict:
        success: bool
        renamed: int
        errors: list[str]
        rollback_failed: bool  (only meaningful when success is False and renamed==0)
        recovery: bool (True if recovery manifest exists after attempt)
    """
    import random

    unhidden = [path.name for path in files if is_hidden(path)]
    random.shuffle(files)
    verbose_log = len(files) < VERBOSE_LOG_THRESHOLD
    log_lines = [
        f"Run started: {datetime.datetime.now().isoformat()}",
        f"Target: {target_dir}",
        f"Files found: {len(files)}",
    ]
    if unhidden:
        log_lines.append(f"Hidden media files unhidden: {len(unhidden)}")
    log_lines.append("")

    errors = []
    shuffling_names = {src.name for src in files}
    occupied = reserved_names(target_dir, shuffling_names)
    if occupied:
        log_lines.append(
            f"Pre-existing names reserved (not shuffled): {len(occupied)}"
        )
    ext_counters: dict[str, int] = {}
    plan: list[dict] = []

    for src in files:
        uid  = uuid.uuid4().hex
        ext  = src.suffix.lower()
        ext_counters[ext] = ext_counters.get(ext, 0) + 1
        preferred = f"{folder_name}-{ext_counters[ext]}{ext}"
        final = allocate_final_name(folder_name, ext, occupied, prefer=preferred)
        plan.append({
            "original": src.name,
            "temp":     f"{TEMP_PREFIX}{uid}{ext}",
            "final":    final,
            "identity": _file_identity(src),
            "was_hidden": is_hidden(src),
        })

    write_log(target_dir, log_lines)
    mp = write_manifest(target_dir, plan)
    log_lines.append(f"Manifest written: {mp}")

    # Pass 1
    temp_map: list[tuple[pathlib.Path, dict]] = []
    pass1_failed = False
    rollback_failed = False

    pass1_ok = 0
    for entry in plan:
        src  = target_dir / entry["original"]
        dest = target_dir / entry["temp"]
        try:
            _checked_rename(src, dest, entry)
            temp_map.append((dest, entry))
            pass1_ok += 1
            if verbose_log:
                log_lines.append(f"PASS1 OK  {entry['original']}  →  {entry['temp']}")
        except (OSError, RuntimeError) as ex:
            errors.append(f"Pass 1 failed: {entry['original']}: {ex}")
            log_lines.append(f"PASS1 ERR {entry['original']}: {ex}")
            pass1_failed = True
            break

    if not verbose_log and pass1_ok:
        log_lines.append(f"PASS1 OK  {pass1_ok} file(s) renamed to temp names")

    if pass1_failed:
        log_lines.append("Pass 1 incomplete — rolling back.")
        rb_failures = []
        for (temp_p, entry) in reversed(temp_map):
            orig_p = target_dir / entry["original"]
            try:
                _checked_rename(temp_p, orig_p, entry)
                if entry["was_hidden"]:
                    set_hidden(orig_p)
                log_lines.append(f"ROLLBACK  {entry['temp']}  →  {entry['original']}")
            except (OSError, RuntimeError) as ex:
                rb_failures.append(f"{entry['temp']}: {ex}")
                log_lines.append(f"ROLLBACK FAIL {entry['temp']}: {ex}")

        if rb_failures:
            errors.extend(rb_failures)
            rollback_failed = True

        write_log(target_dir, log_lines)
        if not rb_failures:
            delete_manifest(target_dir)
        return {
            "success": False,
            "renamed": 0,
            "errors": errors,
            "rollback_failed": rollback_failed,
            "recovery": bool(rb_failures),
        }

    # Keep exactly the persisted mapping. A new destination collision is a
    # recoverable error, never an unjournaled fallback name.
    renamed = 0
    for temp_path, entry in temp_map:
        try:
            _checked_rename(temp_path, target_dir / entry["final"], entry)
            clear_hidden(target_dir / entry["final"])
            renamed += 1
            if verbose_log:
                log_lines.append(f"PASS2 OK {entry['temp']} -> {entry['final']}")
        except (OSError, RuntimeError) as error:
            errors.append(f"Pass 2 rename failed: {error}")
            break

    if not verbose_log and renamed:
        log_lines.append(f"PASS2 OK  {renamed} file(s) renamed to final names")

    log_lines.append(f"\nCompleted: {renamed} renamed, {len(errors)} error(s).")
    write_log(target_dir, log_lines)

    if not errors:
        delete_manifest(target_dir)

    return {
        "success": len(errors) == 0,
        "renamed": renamed,
        "errors": errors,
        "rollback_failed": False,
        "recovery": len(errors) > 0,
    }


# ── Core entry point (no GUI) ────────────────────────────────────────────────

def run_core(target_dir: pathlib.Path, recovery_action: str = "abort", choose_recovery=None) -> dict:
    result = {"success": False, "renamed": 0, "errors": [], "status": "",
              "recovery_performed": False, "recovery_needed": False}
    lock = None
    validated = False
    try:
        target_dir = _validate_target(target_dir)
        validated = True
        folder_name = sanitize_name(target_dir.name)
        if recovery_action not in {"abort", "continue", "rollback", "discard"}:
            raise RuntimeError("Unsupported recovery action.")
        lock = acquire_lock(target_dir)
        preflight(target_dir, folder_name)
        manifest = load_manifest(target_dir)
        if manifest is not None:
            result["recovery_needed"] = True
            if choose_recovery is not None:
                recovery_action = choose_recovery(target_dir, manifest)
            if recovery_action == "abort":
                result["status"] = "Recovery data exists; choose continue or rollback."
                return result
            if recovery_action == "discard":
                delete_manifest(target_dir)
                result.update(success=True, recovery_performed=True,
                              status="Recovery data archived; no media files changed.")
                return result
            if recovery_action == "rollback":
                recovered = do_rollback(target_dir, manifest)
                result.update(success=not recovered["failed"], recovery_performed=True,
                              status=f"Restored {recovered['restored']} file(s).")
                result["errors"] = recovered["failed"]
                if not recovered["failed"]:
                    delete_manifest(target_dir)
                return result
            if recovery_action == "continue":
                recovered = complete_recovery_pass2(target_dir, manifest["entries"], folder_name)
                result.update(success=not recovered["errors"], recovery_performed=True,
                              renamed=recovered["renamed"], status=recovered["status"],
                              errors=recovered["errors"])
                return result
            raise RuntimeError("Recovery action was canceled or invalid.")
        if has_leftover_temps(target_dir):
            result["recovery_needed"] = True
            raise RuntimeError("Orphaned temporary files exist; preserve them and restore manually.")
        files = collect_eligible(target_dir)
        if not files:
            result.update(success=True, status="No eligible media files found.")
            return result
        shuffled = shuffle_rename(target_dir, files, folder_name)
        result.update(success=shuffled["success"], renamed=shuffled["renamed"],
                      errors=shuffled["errors"], status=f"Renamed {shuffled['renamed']} file(s).")
        if shuffled.get("rollback_failed"):
            result["rollback_failed"] = True
    except (OSError, RuntimeError, ValueError) as error:
        result["status"] = str(error)
        result["success"] = False
    finally:
        if lock is not None:
            release_lock(lock)
        if validated:
            try:
                result["recovery_needed"] = manifest_path(target_dir).exists() or has_leftover_temps(target_dir)
            except OSError:
                result["recovery_needed"] = True
    return result


# ── CLI entry point ──────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Shuffle-rename media files on Windows.")
    parser.add_argument("target", nargs="?", help="Target directory")
    parser.add_argument("--quiet", action="store_true", help="No dialogs; emit JSON")
    parser.add_argument("--recovery", choices=["abort", "continue", "rollback", "discard"], default="abort")
    args = parser.parse_args()
    if not args.target:
        result = {"success": False, "status": "No target directory provided."}
    else:
        target_dir = pathlib.Path(args.target)
        result = run_core(target_dir, args.recovery,
                          choose_recovery=None if args.quiet else offer_recovery_gui)
    if args.quiet:
        json.dump(result, sys.stdout, ensure_ascii=True)
        print()
    elif not result["success"]:
        detail = "\n".join(result.get("errors", [])[:10])
        msgbox("Shuffles - Operation stopped", result["status"] + ("\n" + detail if detail else ""), 0x30)
    elif result.get("recovery_performed"):
        msgbox("Shuffles - Recovery", result["status"], 0x40)
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
