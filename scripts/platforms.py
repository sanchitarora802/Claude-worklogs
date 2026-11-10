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
#   choose_many()    a tick-box list (used for "repos not to log"), with the same per-system tools.
#                    Returns the ticked items, None when cancelled, or raises NoPicker.
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


def choose_many(title, prompt, items, ticked):
    if not items:
        return []
    pickers = {"Darwin": [_mac_list], "Windows": [_windows_list], "Linux": [_zenity_list, _kdialog_list]}
    for picker in pickers.get(SYSTEM, []) + [_tk_list]:
        result = picker(title, prompt, items, ticked)
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


def _mac_list(title, prompt, items, ticked):
    listing = "{" + ", ".join(_applescript_text(i) for i in items) + "}"
    chosen = "{" + ", ".join(_applescript_text(i) for i in ticked if i in items) + "}"
    code, out = _run(["osascript",
                      "-e", f"set picked to choose from list {listing} with title {_applescript_text(title)}"
                            f" with prompt {_applescript_text(prompt)} default items {chosen}"
                            " OK button name \"Save\" with multiple selections allowed and empty selection allowed",
                      "-e", 'if picked is false then return "__WORKLOG_CANCEL__"',
                      "-e", "set AppleScript's text item delimiters to linefeed",
                      "-e", "return picked as text"])
    if code is None:
        return "unavailable"
    if code != 0 or out == "__WORKLOG_CANCEL__":
        return None
    return [line for line in out.splitlines() if line in items]


_WINDOWS_LIST_SCRIPT = r"""
Add-Type -AssemblyName System.Windows.Forms
$form = New-Object System.Windows.Forms.Form
$form.Text = $env:WORKLOG_TITLE; $form.Width = 640; $form.Height = 520; $form.StartPosition = 'CenterScreen'
$label = New-Object System.Windows.Forms.Label
$label.Text = $env:WORKLOG_PROMPT; $label.Dock = 'Top'; $label.Height = 48
$list = New-Object System.Windows.Forms.CheckedListBox
$list.Dock = 'Fill'; $list.CheckOnClick = $true
$ticked = $env:WORKLOG_TICKED -split "`n"
foreach ($item in ($env:WORKLOG_ITEMS -split "`n")) { [void]$list.Items.Add($item, ($ticked -contains $item)) }
$panel = New-Object System.Windows.Forms.FlowLayoutPanel
$panel.Dock = 'Bottom'; $panel.Height = 44; $panel.FlowDirection = 'RightToLeft'
$ok = New-Object System.Windows.Forms.Button; $ok.Text = 'Save'; $ok.DialogResult = 'OK'
$cancel = New-Object System.Windows.Forms.Button; $cancel.Text = 'Cancel'; $cancel.DialogResult = 'Cancel'
$panel.Controls.AddRange(@($ok, $cancel)); $form.AcceptButton = $ok; $form.CancelButton = $cancel
$form.Controls.AddRange(@($list, $label, $panel))
if ($form.ShowDialog() -ne 'OK') { exit 1 }
$list.CheckedItems | ForEach-Object { Write-Output $_ }
"""


def _windows_list(title, prompt, items, ticked):
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        return "unavailable"
    env = dict(os.environ, WORKLOG_TITLE=title, WORKLOG_PROMPT=prompt, WORKLOG_ITEMS="\n".join(items),
               WORKLOG_TICKED="\n".join(ticked))
    code, out = _run([shell, "-NoProfile", "-STA", "-Command", _WINDOWS_LIST_SCRIPT], env=env)
    if code is None:
        return "unavailable"
    return [line.strip() for line in out.splitlines() if line.strip() in items] if code == 0 else None


def _zenity_list(title, prompt, items, ticked):
    if not (_has_display() and shutil.which("zenity")):
        return "unavailable"
    rows = []
    for item in items:
        rows += ["TRUE" if item in ticked else "FALSE", item]
    code, out = _run(["zenity", "--list", "--checklist", f"--title={title}", f"--text={prompt}",
                      "--column=Skip", "--column=Folder", "--separator=\n", "--width=640", "--height=480"] + rows)
    return [line for line in out.splitlines() if line in items] if code == 0 else None


def _kdialog_list(title, prompt, items, ticked):
    if not (_has_display() and shutil.which("kdialog")):
        return "unavailable"
    rows = []
    for i, item in enumerate(items):
        rows += [str(i), item, "on" if item in ticked else "off"]
    code, out = _run(["kdialog", "--title", title, "--separate-output", "--checklist", prompt] + rows)
    if code != 0:
        return None
    return [items[int(x)] for x in out.split() if x.isdigit() and int(x) < len(items)]


def _tk_list(title, prompt, items, ticked):
    if SYSTEM == "Linux" and not _has_display():
        return "unavailable"
    try:
        import tkinter
        root = tkinter.Tk()
    except Exception:  # tkinter missing, or no display to open a window on
        return "unavailable"
    root.title(title)
    tkinter.Label(root, text=prompt, justify="left", wraplength=560).pack(anchor="w", padx=12, pady=8)
    states = [tkinter.BooleanVar(root, value=item in ticked) for item in items]
    for item, state in zip(items, states):
        tkinter.Checkbutton(root, text=item, variable=state, anchor="w").pack(fill="x", padx=12)
    result = {"value": None}

    def save():
        result["value"] = [item for item, state in zip(items, states) if state.get()]
        root.destroy()

    buttons = tkinter.Frame(root)
    buttons.pack(fill="x", padx=12, pady=10)
    tkinter.Button(buttons, text="Save", command=save).pack(side="right")
    tkinter.Button(buttons, text="Cancel", command=root.destroy).pack(side="right", padx=6)
    root.mainloop()
    return result["value"]


if __name__ == "__main__":
    print(SYSTEM, machine_name(), sys.version.split()[0])
