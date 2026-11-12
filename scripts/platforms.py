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
#   pick_dates()     a calendar to choose a day or a range of days (used by /worklog:summarise): an
#                    NSDatePicker on macOS and a MonthCalendar on Windows (drag or Shift-click for a
#                    range), zenity/kdialog on Linux (a "from" then a "to" calendar), typed dates in tkinter.
#                    Returns (first, last) dates, None when cancelled, or raises NoPicker.
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
from datetime import date, datetime
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


def pick_dates(title, prompt, initial):
    pickers = {"Darwin": [_mac_date], "Windows": [_windows_date], "Linux": [_zenity_date, _kdialog_date]}
    for picker in pickers.get(SYSTEM, []) + [_tk_date]:
        result = picker(title, prompt, initial)
        if result != "unavailable":
            return result
    raise NoPicker()


def _as_date(text):
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def _as_range(text):
    # "2026-11-01" or "2026-11-01 2026-11-07" (also with ".." or "to" between) -> (first, last)
    parts = [_as_date(p) for p in text.replace("..", " ").replace(" to ", " ").split()]
    if not parts or None in parts or len(parts) > 2:
        return None
    return min(parts), max(parts)


def _two_calendars(one):
    # For tools that pick one day at a time: a "from" calendar, then a "to" calendar.
    first = one("From which day?")
    if not isinstance(first, date):
        return first
    last = one("To which day? (pick the same day for just that day)", first)
    if not isinstance(last, date):
        return last if last == "unavailable" else None
    return min(first, last), max(first, last)


_MAC_DATE_SCRIPT = """
ObjC.import('Cocoa');
function run(argv) {
  var app = $.NSApplication.sharedApplication;
  app.setActivationPolicy(1);  // accessory: a window without a Dock icon
  var picker = $.NSDatePicker.alloc.initWithFrame($.NSMakeRect(0, 0, 280, 160));
  picker.datePickerStyle = 1;          // calendar
  picker.datePickerElements = 0x00e0;  // year, month, day
  picker.datePickerMode = 1;           // range: drag or Shift-click
  picker.dateValue = $.NSDate.dateWithTimeIntervalSince1970(Number(argv[2]));
  picker.maxDate = $.NSDate.date;
  picker.sizeToFit;
  var alert = $.NSAlert.alloc.init;
  alert.messageText = argv[0];
  alert.informativeText = argv[1];
  alert.addButtonWithTitle('Summarise');
  alert.addButtonWithTitle('Cancel');
  alert.accessoryView = picker;
  app.activateIgnoringOtherApps(true);
  if (alert.runModal != 1000) return 'CANCEL';
  var format = $.NSDateFormatter.alloc.init;
  format.dateFormat = 'yyyy-MM-dd';
  var last = picker.dateValue.dateByAddingTimeInterval(picker.timeInterval);
  return ObjC.unwrap(format.stringFromDate(picker.dateValue)) + ' ' + ObjC.unwrap(format.stringFromDate(last));
}
"""


def _mac_date(title, prompt, initial):
    noon = datetime(initial.year, initial.month, initial.day, 12).timestamp()
    code, out = _run(["osascript", "-l", "JavaScript", "-e", _MAC_DATE_SCRIPT, title, prompt, str(int(noon))])
    if code is None or (code != 0 and not out):
        return "unavailable"
    return _as_range(out)


_WINDOWS_DATE_SCRIPT = r"""
Add-Type -AssemblyName System.Windows.Forms
$form = New-Object System.Windows.Forms.Form
$form.Text = $env:WORKLOG_TITLE; $form.AutoSize = $true; $form.StartPosition = 'CenterScreen'
$label = New-Object System.Windows.Forms.Label
$label.Text = $env:WORKLOG_PROMPT; $label.AutoSize = $true; $label.Dock = 'Top'
$calendar = New-Object System.Windows.Forms.MonthCalendar
$calendar.MaxSelectionCount = 31; $calendar.MaxDate = [DateTime]::Today; $calendar.Dock = 'Top'
$calendar.SetDate([DateTime]::ParseExact($env:WORKLOG_START, 'yyyy-MM-dd', $null))
$panel = New-Object System.Windows.Forms.FlowLayoutPanel
$panel.Dock = 'Bottom'; $panel.AutoSize = $true; $panel.FlowDirection = 'RightToLeft'
$ok = New-Object System.Windows.Forms.Button; $ok.Text = 'Summarise'; $ok.DialogResult = 'OK'
$cancel = New-Object System.Windows.Forms.Button; $cancel.Text = 'Cancel'; $cancel.DialogResult = 'Cancel'
$panel.Controls.AddRange(@($ok, $cancel)); $form.AcceptButton = $ok; $form.CancelButton = $cancel
$form.Controls.AddRange(@($panel, $calendar, $label))
if ($form.ShowDialog() -ne 'OK') { exit 1 }
Write-Output ($calendar.SelectionStart.ToString('yyyy-MM-dd') + ' ' + $calendar.SelectionEnd.ToString('yyyy-MM-dd'))
"""


def _windows_date(title, prompt, initial):
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        return "unavailable"
    env = dict(os.environ, WORKLOG_TITLE=title, WORKLOG_PROMPT=prompt, WORKLOG_START=initial.isoformat())
    code, out = _run([shell, "-NoProfile", "-STA", "-Command", _WINDOWS_DATE_SCRIPT], env=env)
    if code is None:
        return "unavailable"
    return _as_range(out) if code == 0 else None


def _zenity_date(title, prompt, initial):
    if not (_has_display() and shutil.which("zenity")):
        return "unavailable"

    def one(question, start=initial):
        code, out = _run(["zenity", "--calendar", f"--title={title}", f"--text={question}",
                          "--date-format=%Y-%m-%d", f"--day={start.day}", f"--month={start.month}",
                          f"--year={start.year}"])
        return _as_date(out) if code == 0 else None
    return _two_calendars(one)


def _kdialog_date(title, prompt, initial):
    if not (_has_display() and shutil.which("kdialog")):
        return "unavailable"

    def one(question, start=initial):
        code, out = _run(["kdialog", "--title", title, "--dateformat", "yyyy-MM-dd", "--calendar", question])
        return _as_date(out) if code == 0 else None
    return _two_calendars(one)


def _tk_date(title, prompt, initial):
    # tkinter has no calendar widget, so this asks for the date as text.
    if SYSTEM == "Linux" and not _has_display():
        return "unavailable"
    try:
        import tkinter
        from tkinter import simpledialog
        root = tkinter.Tk()
    except Exception:  # tkinter missing, or no display to open a window on
        return "unavailable"
    root.withdraw()
    try:
        text = simpledialog.askstring(title, "Day to summarise (YYYY-MM-DD), or a range (FROM..TO):",
                                      initialvalue=initial.isoformat(), parent=root)
        return _as_range(text) if text else None
    finally:
        root.destroy()


if __name__ == "__main__":
    print(SYSTEM, machine_name(), sys.version.split()[0])
