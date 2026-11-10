# Everything that differs between macOS, Windows and Linux, so the rest of the code doesn't care.
#
#   machine_name()   name of this computer, used for its subfolder in the log folder
#   locked(path)     an exclusive file lock (fcntl on macOS/Linux, msvcrt on Windows)
#   detached()       Popen arguments that let a child keep running after its parent exits
#   cli_names()      file names of the `claude` program on this system
#   pick_folder()    a native "note, then choose a folder" dialog:
#                      macOS    osascript (Finder dialog with a Help button)
#                      Windows  PowerShell + Windows Forms
#                      Linux    zenity or kdialog, when a desktop session is running
#                      any      Python's tkinter, when none of the above is available
#                    Returns a Path, None when the user cancelled, or raises NoPicker when this
#                    system has no way to show a dialog (then setup asks for a typed path).
#
# Only the macOS paths have been run; Windows and Linux are written to their documented APIs
# but untested.
import contextlib
import os
import platform
import shutil
import socket
import subprocess
import sys
import webbrowser
from pathlib import Path

SYSTEM = platform.system()  # "Darwin", "Windows" or "Linux"
PICKER_PROMPT = "Choose a folder that syncs between your own computers (not one shared with other people)"


class NoPicker(Exception):
    pass


def machine_name():
    if SYSTEM == "Darwin":
        try:
            name = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True,
                                  timeout=5).stdout.strip()
            if name:
                return name
        except (OSError, subprocess.SubprocessError):
            pass
    if SYSTEM == "Windows" and os.environ.get("COMPUTERNAME"):
        return os.environ["COMPUTERNAME"]
    return socket.gethostname().split(".")[0] or "unknown"


@contextlib.contextmanager
def locked(path):
    with open(path, "a+") as handle:
        if SYSTEM == "Windows":
            import msvcrt
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)  # retries for ~10 s, then raises
                    break
                except OSError:
                    continue
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield


def detached():
    if SYSTEM == "Windows":
        return {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def cli_names():
    return ["claude.exe", "claude"] if SYSTEM == "Windows" else ["claude"]


def pick_folder(current, note, help_url):
    start = str(current if current and current.is_dir() else Path.home())
    pickers = {"Darwin": [_mac], "Windows": [_windows], "Linux": [_zenity, _kdialog]}.get(SYSTEM, [])
    for picker in pickers + [_tk]:
        result = picker(start, note, help_url)
        if result != "unavailable":
            return result
    raise NoPicker()


def _run(args, **kwargs):
    try:
        done = subprocess.run(args, capture_output=True, text=True, **kwargs)
        return done.returncode, done.stdout.strip()
    except OSError:
        return None, ""


def _applescript_text(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _mac(start, note, help_url):
    while True:
        code, out = _run(["osascript", "-e",
                          "display dialog " + _applescript_text(note)
                          + ' with title "Worklog setup" with icon note'
                          + ' buttons {"Help", "Cancel", "Choose Folder…"} default button "Choose Folder…"'
                          + ' cancel button "Cancel"'])
        if code is None:
            return "unavailable"
        if code != 0:
            return None
        if "Help" not in out:
            break
        webbrowser.open(help_url)
    code, out = _run(["osascript", "-e", "POSIX path of (choose folder with prompt "
                      + _applescript_text(PICKER_PROMPT) + " default location (POSIX file "
                      + _applescript_text(start) + "))"])
    return Path(out.rstrip("/")) if code == 0 and out else None


_WINDOWS_SCRIPT = r"""
Add-Type -AssemblyName System.Windows.Forms
$answer = [System.Windows.Forms.MessageBox]::Show($env:WORKLOG_NOTE, 'Worklog setup', 'OKCancel', 'Information')
if ($answer -ne 'OK') { exit 1 }
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = $env:WORKLOG_PROMPT
$dialog.SelectedPath = $env:WORKLOG_START
if ($dialog.ShowDialog() -eq 'OK') { Write-Output $dialog.SelectedPath } else { exit 1 }
"""


def _windows(start, note, help_url):
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        return "unavailable"
    env = dict(os.environ, WORKLOG_NOTE=f"{note}\n\nMore help: {help_url}\n\nPress OK to choose the folder.",
               WORKLOG_PROMPT=PICKER_PROMPT, WORKLOG_START=start)
    code, out = _run([shell, "-NoProfile", "-STA", "-Command", _WINDOWS_SCRIPT], env=env)
    return Path(out) if code == 0 and out else None


def _has_display():
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _zenity(start, note, help_url):
    if not (_has_display() and shutil.which("zenity")):
        return "unavailable"
    code, _ = _run(["zenity", "--question", "--title=Worklog setup", "--no-wrap",
                    f"--text={note}\n\nMore help: {help_url}", "--ok-label=Choose Folder…"])
    if code != 0:
        return None
    code, out = _run(["zenity", "--file-selection", "--directory", f"--title={PICKER_PROMPT}",
                      f"--filename={start.rstrip('/')}/"])
    return Path(out) if code == 0 and out else None


def _kdialog(start, note, help_url):
    if not (_has_display() and shutil.which("kdialog")):
        return "unavailable"
    code, _ = _run(["kdialog", "--title", "Worklog setup", "--yesno", f"{note}\n\nMore help: {help_url}",
                    "--yes-label", "Choose Folder…", "--no-label", "Cancel"])
    if code != 0:
        return None
    code, out = _run(["kdialog", "--title", PICKER_PROMPT, "--getexistingdirectory", start])
    return Path(out) if code == 0 and out else None


def _tk(start, note, help_url):
    if SYSTEM == "Linux" and not _has_display():
        return "unavailable"
    try:
        import tkinter
        from tkinter import filedialog, messagebox
        root = tkinter.Tk()
    except Exception:  # tkinter missing, or no display to open a window on
        return "unavailable"
    root.withdraw()
    try:
        if not messagebox.askokcancel("Worklog setup", f"{note}\n\nMore help: {help_url}", parent=root):
            return None
        chosen = filedialog.askdirectory(title=PICKER_PROMPT, initialdir=start, mustexist=True, parent=root)
        return Path(chosen) if chosen else None
    finally:
        root.destroy()


if __name__ == "__main__":
    print(SYSTEM, machine_name(), sys.version.split()[0])
