# Shuffles

A small Windows utility I use every day to randomize the filename order of images and videos in a folder. It renames files; it does not shuffle their pixels, change their contents, or guarantee how every image viewer sorts them. Select sorting by filename in your viewer.

For example, a folder named `Holiday Photos` becomes:

```text
holiday-photos-1.jpg
holiday-photos-2.jpg
holiday-photos-1.png
```

The order is randomized on every run. Each extension has its own numbering sequence. The folder name is lowercased, whitespace becomes dashes, and the base is limited to 120 characters. Subfolders and unsupported files stay unchanged.

**Renaming changes your original filenames. Keep an independent backup and try a disposable copy first.** Recovery journals help with interrupted runs; they are not a backup or a guarantee against power loss, disk failure, external edits or hostile concurrent processes.

## Requirements and supported files

Windows, Python 3.10+ with `pythonw.exe`, and a trusted local writable folder. There are no third-party Python dependencies. The automated core checks run on Windows with Python 3.14.

```text
.jpg .jpeg .png .gif .bmp .tif .tiff .webp .heic .heif .avif
.mp4 .mov .avi .mkv .wmv .flv .webm .m4v
```

Hidden eligible files are included and unhidden when assigned their final filenames. Files with hard links, reparse points, or offline/recall attributes are skipped. Drive roots, the profile root, Windows/program folders, UNC/device paths and paths through reparse/offline folders are refused. Cloud-synced folders, network/removable drives and concurrent writers are outside the supported workflow; attribute checks cannot identify every sync provider.

## Use without installation

Use a fully qualified folder path. For example, after creating a disposable test folder:

```powershell
python .\shuffles.py "C:\ShuffleDemo\Photos" --quiet
```

`--quiet` suppresses dialogs, prints a JSON result and returns exit code `0` on success or `1` on failure. Without it, failures and recovery choices use Windows dialogs; successful ordinary shuffles are silent. The script does not recurse into subfolders or decode media.

## Optional Explorer menu

Review the scripts, then run `installer.ps1` with PowerShell and confirm the installation. It copies runtime files to `%LOCALAPPDATA%\Programs\Shuffles` and registers **Organize this folder** under the current user's Explorer background menu (HKCU). Windows 11 may place it under **Show more options**. Normal installation does not need administrator access.

The registered command pins an absolute `pythonw.exe` path and the installed script. Moving the source checkout does not affect that copy. Source edits require running the installer again to update the installed runtime. Resolve pending recovery with the original installed version before upgrading.

For a reviewed silent repair/update:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup\repair-paths.ps1
```

The repair script generates interpreter paths and setup/launcher files only in the installation directory. These local generated files are ignored by Git. The menu uses the built-in Windows folder icon; no custom artwork is needed.

`uninstaller.ps1` removes the per-user menu registration and generated setup files after confirmation. It retains the runtime code. `setup/install-legacy-bridge.ps1 -LegacyLauncher <old-launcher-path>` is an optional, explicit compatibility tool for an obsolete launcher; it refuses to replace a different existing file. It is unnecessary for a fresh install.

## Interrupted runs

A lock is acquired before inspecting recovery data or asking for a recovery choice. A version-2 JSON journal is written atomically in `%TEMP%` before the first rename. It records the original, temporary and final names plus each file's identity, size, modification time and hidden flag. The first pass stages every file under a short unique name; the second assigns the planned final names.

On Windows, occupied destinations stop renaming rather than being overwritten. Recovery checks identity rather than trusting filenames, then stages the entire set again to handle name cycles. Missing, replaced or modified files and unrelated destination collisions stop recovery and preserve its journal.

GUI recovery offers **Yes** to restore original names, **No** to finish the planned names, and **Cancel** to stop. Quiet recovery is explicit:

```powershell
python .\shuffles.py "C:\ShuffleDemo\Photos" --quiet --recovery=rollback
python .\shuffles.py "C:\ShuffleDemo\Photos" --quiet --recovery=continue
```

The default `--recovery=abort` leaves media untouched when a journal exists. `--recovery=discard` archives only a validated journal and does not rename media or start a new shuffle. Discarding does not repair orphaned temporary files. Corrupt, legacy version-1, or mismatched journals are preserved and block a fresh run; use the original version or restore from backup rather than guessing a mapping.

Completed or discarded journals move to `%TEMP%\.shuffles-archive`. A hard termination can leave `.shuffles.lock` in the target folder. Confirm that no Shuffles process is operating on that folder before manually archiving that stale lock. Never clear it automatically while another writer may be active. Keep the folder at the same absolute path while recovering.

## Local metadata and privacy

The hidden `.shuffles_last.log` contains the target path and operation details. Journals and their archives contain paths, filenames and filesystem metadata. They stay local; Shuffles has no network client or telemetry. Temp-folder cleanup can remove journals, so keep your own backup. Treat these files as private and avoid attaching them unredacted to public bug reports. See [SECURITY.md](SECURITY.md).

## Tests and limits of verification

```powershell
python -m unittest discover -s tests -v
```

Core tests create synthetic fixtures under the ignored `_test_artifacts` folder, preserve those fixtures for inspection, and never use personal media, launch dialogs, install software or change the registry. They cover collision refusal, hidden files, directory reservations, malformed journals, file identity, locking, interrupted passes, rollback and continuation.

Installed-runtime tests are skipped by default. Setting `SHUFFLES_RUN_INSTALLED_TESTS=1` opts into tests that read the live registration and launch the installed program on synthetic media. They are separate from the offline core suite and were not run as part of this public-source preparation. Explorer appearance, installer execution and real-media behavior have not been certified by these tests.

## License

MIT. See [LICENSE](LICENSE).
