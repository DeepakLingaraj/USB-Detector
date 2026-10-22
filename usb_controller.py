import tkinter as tk
from tkinter import messagebox, ttk
import subprocess
import sys
import ctypes
import os
import tempfile
import webbrowser
import json
import time
import threading
import queue

# --------------------------------------------------
# REQUIRED: Pillow for loading images
# --------------------------------------------------
try:
    from PIL import Image, ImageTk
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False
    print("Pillow not installed. Install it with: pip install Pillow")

# =====================================================
# 🔑 CHANGE THIS PASSWORD TO YOUR OWN SECRET PASSWORD
# =====================================================
PASSWORD = "qwerty"

# =====================================================
# 🎨 THEME — a single cohesive palette
# =====================================================
BG_DARK        = "#070a12"
BG_DARK_2      = "#0c1120"
BG_PANEL       = "#0e1422"
BG_PANEL_EDGE  = "#1c273d"
BG_INPUT       = "#121a2c"

ACCENT         = "#38bdf8"
ACCENT_DIM     = "#12314a"

DANGER         = "#fb7185"
DANGER_DIM     = "#3a1420"

SUCCESS        = "#34d399"
SUCCESS_DIM    = "#0c2e24"

TEXT_PRIMARY   = "#eef2f8"
TEXT_MUTED     = "#647089"

FONT_MAIN      = ("Segoe UI", 11)
FONT_TITLE     = ("Segoe UI Semibold", 18)
FONT_LABEL_SM  = ("Consolas", 9)
FONT_BTN       = ("Segoe UI Semibold", 12)
FONT_BTN_SUB   = ("Consolas", 8)

# =====================================================
# 🖼️ TOP-RIGHT IMAGE
# =====================================================
TOP_IMAGE_PATH = "usb-png.png"
LOGO_SIZE = (34, 34)

STATE = {"usb_enabled": True, "fullscreen": False}

# Holds a reference to the currently open "Connected USB Devices" tree
# so the background monitor can push live updates into it.
devices_window_ref = {"tree": None}

# Cross-thread communication for USB connect/disconnect events
usb_event_queue = queue.Queue()

# =====================================================
# HELPER: Get the correct base path
# =====================================================
def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

# =====================================================
# 🖼️ ICON REMOVAL
# =====================================================
def remove_feather_icon(root_window):
    temp_path = None
    if PILLOW_AVAILABLE:
        try:
            blank_img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
            with tempfile.NamedTemporaryFile(delete=False, suffix=".ico") as f:
                blank_img.save(f, format="ICO", sizes=[(16, 16), (32, 32)])
                temp_path = f.name
            root_window.iconbitmap(temp_path)
            root_window.after(200, lambda: os.unlink(temp_path) if temp_path and os.path.exists(temp_path) else None)
        except Exception:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except:
                    pass
    if sys.platform == "win32":
        try:
            root_window.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(root_window.winfo_id())
            WM_SETICON = 0x0080
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 0, 0)
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 1, 0)
        except Exception:
            pass

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def set_usb_state(state, success_msg, enabled_flag):
    try:
        result = subprocess.run(
            [
                "reg", "add",
                "HKLM\\SYSTEM\\CurrentControlSet\\Services\\USBSTOR",
                "/v", "Start",
                "/t", "REG_DWORD",
                "/d", str(state),
                "/f"
            ],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        if result.returncode == 0:
            STATE["usb_enabled"] = enabled_flag
            update_status_led()
            messagebox.showinfo("System / Success", success_msg)
        else:
            error_details = result.stderr.strip() if result.stderr else "Unknown error occurred."
            messagebox.showerror("System / Error", f"Registry update failed.\n\n{error_details}")
    except Exception as e:
        messagebox.showerror("System / Error", str(e))

# =====================================================
# 🔌 USB DEVICE DETAILS
# =====================================================
def get_usb_devices():
    """
    Query all currently connected USB devices via PowerShell/WMI.
    Returns a list of dicts with Name, DeviceID, Manufacturer, Status, PNPClass.
    Returns [] on non-Windows platforms or if the query fails.
    """
    if sys.platform != "win32":
        return []
    try:
        ps_command = (
            "Get-CimInstance -ClassName Win32_PnPEntity | "
            "Where-Object { $_.DeviceID -like 'USB*' -and $_.Present -eq $true } | "
            "Select-Object Name, DeviceID, Manufacturer, Status, PNPClass | "
            "ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_command],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            data = [data]
        # Normalize missing fields
        for d in data:
            d.setdefault("Name", "Unknown device")
            d.setdefault("Manufacturer", "Unknown manufacturer")
            d.setdefault("DeviceID", "N/A")
            d.setdefault("Status", "N/A")
        return data
    except Exception as e:
        print(f"USB query error: {e}")
        return []

def usb_monitor_thread():
    """Background thread: polls connected USB devices and reports changes."""
    known = {d.get("DeviceID"): d for d in get_usb_devices()}
    while True:
        time.sleep(3)
        current = {d.get("DeviceID"): d for d in get_usb_devices()}
        new_ids = set(current) - set(known)
        removed_ids = set(known) - set(current)
        for did in new_ids:
            usb_event_queue.put(("connected", current[did]))
        for did in removed_ids:
            usb_event_queue.put(("disconnected", known[did]))
        known = current

def poll_usb_queue():
    """Runs on the Tk main thread; drains events from the monitor thread."""
    try:
        while True:
            event, device = usb_event_queue.get_nowait()
            handle_usb_event(event, device)
    except queue.Empty:
        pass
    root.after(1000, poll_usb_queue)

def handle_usb_event(event, device):
    name = device.get("Name", "Unknown device")
    manufacturer = device.get("Manufacturer", "Unknown manufacturer")
    device_id = device.get("DeviceID", "N/A")
    status = device.get("Status", "N/A")

    if event == "connected":
        title = "🔌 USB Device Connected"
    else:
        title = "🔌 USB Device Disconnected"

    msg = (
        f"Name: {name}\n"
        f"Manufacturer: {manufacturer}\n"
        f"Status: {status}\n"
        f"Device ID:\n{device_id}"
    )
    messagebox.showinfo(title, msg)

    # Live-refresh the devices window if it's open
    tree = devices_window_ref.get("tree")
    if tree is not None:
        refresh_device_tree(tree)

def refresh_device_tree(tree):
    for row in tree.get_children():
        tree.delete(row)
    devices = get_usb_devices()
    if not devices:
        tree.insert("", "end", values=("No USB devices found", "", "", ""))
        return
    for d in devices:
        tree.insert("", "end", values=(
            d.get("Name", "Unknown device"),
            d.get("Manufacturer", "Unknown manufacturer"),
            d.get("DeviceID", "N/A"),
            d.get("Status", "N/A"),
        ))

def show_usb_devices_window():
    win = tk.Toplevel(root)
    win.title("Connected USB Devices")
    win.configure(bg=BG_DARK_2)
    win.geometry("760x440")
    win.minsize(560, 340)
    win.transient(root)

    header = tk.Label(win, text="🔌 CONNECTED USB DEVICES", font=("Segoe UI", 16, "bold"),
                       bg=BG_DARK_2, fg=ACCENT)
    header.pack(pady=(16, 8))

    list_frame = tk.Frame(win, bg=BG_DARK_2)
    list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))

    style = ttk.Style(win)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("Treeview",
                     background=BG_INPUT, fieldbackground=BG_INPUT,
                     foreground=TEXT_PRIMARY, rowheight=24, borderwidth=0)
    style.configure("Treeview.Heading",
                     background=BG_PANEL, foreground=ACCENT,
                     font=("Segoe UI", 9, "bold"), relief="flat")
    style.map("Treeview", background=[("selected", ACCENT_DIM)])

    columns = ("name", "manufacturer", "device_id", "status")
    tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=12)
    tree.heading("name", text="Device Name")
    tree.heading("manufacturer", text="Manufacturer")
    tree.heading("device_id", text="Device ID")
    tree.heading("status", text="Status")
    tree.column("name", width=220)
    tree.column("manufacturer", width=160)
    tree.column("device_id", width=260)
    tree.column("status", width=80, anchor="center")

    scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    tree.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    devices_window_ref["tree"] = tree

    btn_refresh = NeonButton(win, text="REFRESH", command=lambda: refresh_device_tree(tree),
                              width=200, height=40, accent=ACCENT, accent_dim=ACCENT_DIM,
                              font=FONT_MAIN)
    btn_refresh.pack(pady=(0, 16))

    refresh_device_tree(tree)

    def on_close():
        devices_window_ref["tree"] = None
        win.destroy()
    win.protocol("WM_DELETE_WINDOW", on_close)

# =====================================================
# 🔒 FIXED AUTHENTICATION DIALOG (fits perfectly, button always visible)
# =====================================================
def prompt_and_run(state, success_msg, enabled_flag):
    # Create a Toplevel that exactly covers the main window
    pw_win = tk.Toplevel(root)
    pw_win.title("Access Control")
    pw_win.configure(bg=BG_DARK_2)
    pw_win.grab_set()
    pw_win.transient(root)
    pw_win.attributes("-topmost", True)

    # Get main window geometry and set the dialog to cover it exactly
    root.update_idletasks()
    x = root.winfo_x()
    y = root.winfo_y()
    w = root.winfo_width()
    h = root.winfo_height()
    pw_win.geometry(f"{w}x{h}+{x}+{y}")

    # Allow the user to cancel by closing the window
    def on_close():
        pw_win.destroy()
    pw_win.protocol("WM_DELETE_WINDOW", on_close)

    # Main inner frame – fills the entire dialog
    inner = tk.Frame(pw_win, bg=BG_PANEL, highlightthickness=2,
                     highlightbackground=ACCENT)
    inner.pack(expand=True, fill="both")

    # Content width is capped/scaled to the window, but height is left
    # unspecified so the frame always sizes itself to fit ALL of its
    # children (title, entry, error label, AUTHORIZE button). This is
    # what was cutting the button off in non-fullscreen/smaller windows.
    max_w = 600
    min_w = 350
    panel_w = max(min_w, min(int(w * 0.7), max_w))

    content = tk.Frame(inner, bg=BG_PANEL)
    content.place(relx=0.5, rely=0.5, anchor="center", width=panel_w)

    # Title
    tk.Label(content, text="🔐 AUTHENTICATION REQUIRED",
             font=("Segoe UI", 22, "bold"),
             bg=BG_PANEL, fg=ACCENT).pack(pady=(24, 8))

    tk.Label(content, text="Enter administrator passcode",
             font=("Segoe UI", 14),
             bg=BG_PANEL, fg=TEXT_PRIMARY).pack(pady=(0, 12))

    # Entry – expands width but has a max
    entry_frame = tk.Frame(content, bg=BG_PANEL)
    entry_frame.pack(pady=8, fill="x", padx=40)
    entry = tk.Entry(entry_frame, show="●",
                     font=("Segoe UI", 18),
                     bg=BG_INPUT, fg=TEXT_PRIMARY,
                     insertbackground=ACCENT,
                     relief="flat", justify="center",
                     highlightthickness=2,
                     highlightbackground=BG_PANEL_EDGE,
                     highlightcolor=ACCENT)
    entry.pack(fill="x", ipady=10)
    entry.focus()

    error_label = tk.Label(content, text="", fg=DANGER, bg=BG_PANEL,
                           font=("Segoe UI", 11, "bold"))
    error_label.pack(pady=4)

    def on_ok():
        if entry.get() == PASSWORD:
            pw_win.destroy()
            set_usb_state(state, success_msg, enabled_flag)
        else:
            error_label.config(text="✗ Access denied — invalid passcode")
            entry.delete(0, tk.END)
            entry.focus()

    def on_enter(event):
        on_ok()

    # Button – always fully visible since content's height is now
    # driven by its children instead of a hard-coded fraction of the window.
    btn_frame = tk.Frame(content, bg=BG_PANEL)
    btn_frame.pack(pady=(12, 20), fill="x", padx=50)
    ok_btn = NeonButton(btn_frame, text="AUTHORIZE",
                        width=300, height=56,
                        accent=ACCENT, accent_dim=ACCENT_DIM,
                        command=on_ok,
                        font=("Segoe UI", 14, "bold"))
    ok_btn.pack(pady=5)

    pw_win.bind("<Return>", on_enter)

def disable_usb():
    if not is_admin():
        try:
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
            root.destroy()
            return
        except Exception:
            messagebox.showerror("Admin required", "Please right-click and select 'Run as administrator'.")
            return
    prompt_and_run(4, "USB ports have been disabled.", False)

def enable_usb():
    if not is_admin():
        try:
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
            root.destroy()
            return
        except Exception:
            messagebox.showerror("Admin required", "Please right-click and select 'Run as administrator'.")
            return
    prompt_and_run(3, "USB ports have been enabled.", True)

# =====================================================
# 🖥️ FULLSCREEN TOGGLE
# =====================================================
def toggle_fullscreen(event=None):
    STATE["fullscreen"] = not STATE["fullscreen"]
    root.attributes("-fullscreen", STATE["fullscreen"])
    fs_btn.config_text("EXIT FULLSCREEN   (Esc)" if STATE["fullscreen"] else "FULLSCREEN   (F11)")

def exit_fullscreen(event=None):
    if STATE["fullscreen"]:
        STATE["fullscreen"] = False
        root.attributes("-fullscreen", False)
        fs_btn.config_text("FULLSCREEN   (F11)")

# =====================================================
# 🧩 CUSTOM WIDGETS
# =====================================================

class NeonButton(tk.Canvas):
    """Ghost-style button with hover effect."""
    def __init__(self, parent, text, command, width=220, height=48,
                 accent=ACCENT, accent_dim=ACCENT_DIM,
                 font=FONT_BTN, radius=10, subtitle=None):
        super().__init__(parent, width=width, height=height, bg=BG_PANEL,
                          highlightthickness=0, bd=0, cursor="hand2")
        self.command = command
        self.accent = accent
        self.accent_dim = accent_dim
        self.txt = text
        self.subtitle = subtitle
        self.w = width
        self.h = height
        self.radius = radius
        self.font = font
        self.hovered = False
        self._draw()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _round_rect(self, x1, y1, x2, y2, r, **kwargs):
        points = [
            x1+r, y1, x2-r, y1, x2, y1, x2, y1+r, x2, y2-r, x2, y2,
            x2-r, y2, x1+r, y2, x1, y2, x1, y2-r, x1, y1+r, x1, y1
        ]
        return self.create_polygon(points, smooth=True, **kwargs)

    def _draw(self):
        self.delete("all")
        if self.hovered:
            self._round_rect(1, 1, self.w-1, self.h-1, self.radius+2,
                              outline=self.accent_dim, fill="", width=4)
            fill = self.accent
            outline = self.accent
            text_color = BG_DARK
            sub_color = BG_DARK
        else:
            fill = BG_PANEL
            outline = self.accent_dim
            text_color = self.accent
            sub_color = TEXT_MUTED

        self._round_rect(3, 3, self.w-3, self.h-3, self.radius, fill=fill, outline=outline, width=1.5)
        ty = self.h//2 if not self.subtitle else self.h//2 - 8
        self.create_text(self.w//2, ty, text=self.txt, fill=text_color, font=self.font)
        if self.subtitle:
            self.create_text(self.w//2, ty+17, text=self.subtitle, fill=sub_color, font=FONT_BTN_SUB)

    def config_text(self, new_text):
        self.txt = new_text
        self._draw()

    def _on_enter(self, e):
        self.hovered = True
        self._draw()

    def _on_leave(self, e):
        self.hovered = False
        self._draw()

    def _on_click(self, e):
        if self.command:
            self.command()


def draw_gradient_bg(canvas, w, h):
    """Vertical gradient background."""
    canvas.delete("gradient")
    steps = 40
    c1 = canvas.winfo_rgb(BG_DARK)
    c2 = canvas.winfo_rgb(BG_DARK_2)
    for i in range(steps):
        t = i / steps
        r = int(c1[0] + (c2[0]-c1[0])*t) >> 8
        g = int(c1[1] + (c2[1]-c1[1])*t) >> 8
        b = int(c1[2] + (c2[2]-c1[2])*t) >> 8
        color = f"#{r:02x}{g:02x}{b:02x}"
        y0 = int(h * i / steps)
        y1 = int(h * (i+1) / steps) + 1
        canvas.create_rectangle(0, y0, w, y1, fill=color, outline="", tags="gradient")
    canvas.tag_lower("gradient")

# =====================================================
# 🚀 SPLASH SCREEN WITH ANIMATION
# =====================================================
def show_splash(root):
    splash = tk.Toplevel(root)
    splash.title("Starting...")
    splash.overrideredirect(True)
    splash.configure(bg=BG_DARK)

    sw = 500
    sh = 300
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    x = (screen_w - sw) // 2
    y = (screen_h - sh) // 2
    splash.geometry(f"{sw}x{sh}+{x}+{y}")

    frame = tk.Frame(splash, bg=BG_PANEL, highlightthickness=1, highlightbackground=BG_PANEL_EDGE)
    frame.place(relx=0.5, rely=0.5, anchor="center", width=sw-20, height=sh-20)

    tk.Label(frame, text="USB Port Controller", font=("Segoe UI", 20, "bold"),
             bg=BG_PANEL, fg=TEXT_PRIMARY).pack(pady=(30, 5))

    tk.Label(frame, text="Loading hardware module...", font=FONT_MAIN,
             bg=BG_PANEL, fg=TEXT_MUTED).pack(pady=(0, 20))

    canvas = tk.Canvas(frame, width=360, height=26, bg=BG_INPUT, highlightthickness=0)
    canvas.pack(pady=10)

    canvas.create_rectangle(5, 5, 355, 21, fill=BG_INPUT, outline=BG_PANEL_EDGE, width=1)
    progress = canvas.create_rectangle(5, 5, 5, 21, fill=ACCENT, outline="", width=0)

    percent_label = tk.Label(frame, text="0%", font=("Segoe UI", 10, "bold"),
                             bg=BG_PANEL, fg=ACCENT)
    percent_label.pack(pady=(5, 0))

    msg_label = tk.Label(frame, text="Initializing...", font=FONT_LABEL_SM,
                         bg=BG_PANEL, fg=TEXT_MUTED)
    msg_label.pack(pady=(10, 0))

    def update_progress(step=0):
        if step <= 100:
            width = 5 + (step / 100) * 350
            canvas.coords(progress, 5, 5, width, 21)
            percent_label.config(text=f"{step}%")

            if step < 30:
                msg_label.config(text="Loading drivers...")
            elif step < 60:
                msg_label.config(text="Checking system state...")
            elif step < 85:
                msg_label.config(text="Preparing interface...")
            else:
                msg_label.config(text="Almost ready...")

            splash.after(20, update_progress, step + 1)
        else:
            splash.destroy()
            root.deiconify()

    update_progress(0)
    splash.attributes("-topmost", True)
    splash.grab_set()
    splash.wait_window()

# =====================================================
# MAIN GUI WINDOW
# =====================================================

root = tk.Tk()
root.title("USB Port Controller")
root.withdraw()

WIN_W, WIN_H = 640, 520
root.geometry(f"{WIN_W}x{WIN_H}")
root.minsize(480, 420)
root.resizable(True, True)
root.configure(bg=BG_DARK)

show_splash(root)

remove_feather_icon(root)
root.bind("<F11>", toggle_fullscreen)
root.bind("<Escape>", exit_fullscreen)

bg_canvas = tk.Canvas(root, bg=BG_DARK, highlightthickness=0)
bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)

def redraw_background(event=None):
    w = max(root.winfo_width(), 1)
    h = max(root.winfo_height(), 1)
    draw_gradient_bg(bg_canvas, w, h)
    bg_canvas.delete("bracket")
    L = 22
    for (x1, y1, x2, y2) in [
        (14, 14, 14+L, 14), (14, 14, 14, 14+L),
        (w-14, 14, w-14-L, 14), (w-14, 14, w-14, 14+L),
        (14, h-14, 14+L, h-14), (14, h-14, 14, h-14-L),
        (w-14, h-14, w-14-L, h-14), (w-14, h-14, w-14, h-14-L),
    ]:
        bg_canvas.create_line(x1, y1, x2, y2, fill=BG_PANEL_EDGE, width=2, tags="bracket")

def clamp_panel(event=None):
    w = max(root.winfo_width(), 1)
    h = max(root.winfo_height(), 1)
    target_w = min(int(w*0.85), 620)
    target_h = min(int(h*0.85), 520)
    panel.place(relx=0.5, rely=0.5, anchor="center", width=target_w, height=target_h)

def on_resize(event=None):
    redraw_background(event)
    clamp_panel(event)

root.bind("<Configure>", on_resize)

panel = tk.Frame(root, bg=BG_PANEL, highlightthickness=1, highlightbackground=BG_PANEL_EDGE)
panel.place(relx=0.5, rely=0.5, anchor="center", width=620, height=520)

header = tk.Frame(panel, bg=BG_PANEL)
header.pack(fill="x", pady=(22, 4), padx=26)

title_label = tk.Label(header, text="USB Port Controller", font=FONT_TITLE,
                        bg=BG_PANEL, fg=TEXT_PRIMARY)
title_label.pack(side="left")

logo_label_ref = {"widget": None}

def load_top_image(path):
    if not PILLOW_AVAILABLE:
        return
    if logo_label_ref["widget"] is not None:
        logo_label_ref["widget"].destroy()
        logo_label_ref["widget"] = None
    try:
        img = Image.open(path)
        img = img.resize(LOGO_SIZE, Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        root.image_ref = photo
        lbl = tk.Label(header, image=photo, bg=BG_PANEL)
        lbl.pack(side="right")
        logo_label_ref["widget"] = lbl
    except FileNotFoundError:
        print(f"Warning: '{path}' not found - skipping image.")
    except Exception as e:
        print(f"Warning: Could not load image - {e}")

load_top_image(TOP_IMAGE_PATH)

subtitle = tk.Label(panel, text="Hardware access module", font=FONT_LABEL_SM,
                     bg=BG_PANEL, fg=TEXT_MUTED)
subtitle.pack(anchor="w", padx=28, pady=(0, 10))

divider = tk.Frame(panel, bg=BG_PANEL_EDGE, height=1)
divider.pack(fill="x", padx=26, pady=(0, 18))

status_row = tk.Frame(panel, bg=BG_PANEL)
status_row.pack(fill="x", padx=28, pady=(0, 22))

status_canvas = tk.Canvas(status_row, width=140, height=30, bg=BG_PANEL, highlightthickness=0)
status_canvas.pack(side="left")

def draw_status_pill(active):
    status_canvas.delete("all")
    color = SUCCESS if active else DANGER
    dim = SUCCESS_DIM if active else DANGER_DIM
    status_canvas.create_oval(2, 2, 138, 28, outline=dim, fill=BG_INPUT, width=1)
    status_canvas.create_oval(12, 11, 22, 21, fill=color, outline="")
    status_canvas.create_text(80, 15, text=("USB ACTIVE" if active else "USB LOCKED"),
                               fill=color, font=FONT_LABEL_SM)

def update_status_led():
    draw_status_pill(STATE["usb_enabled"])

btn_row = tk.Frame(panel, bg=BG_PANEL)
btn_row.pack(pady=(0, 16))

btn_disable = NeonButton(btn_row, text="DISABLE USB", command=disable_usb,
                          width=260, height=56, accent=DANGER, accent_dim=DANGER_DIM,
                          subtitle="Lock all ports")
btn_disable.grid(row=0, column=0, padx=8, pady=6)

btn_enable = NeonButton(btn_row, text="ENABLE USB", command=enable_usb,
                         width=260, height=56, accent=SUCCESS, accent_dim=SUCCESS_DIM,
                         subtitle="Unlock all ports")
btn_enable.grid(row=0, column=1, padx=8, pady=6)

btn_devices = NeonButton(panel, text="CONNECTED USB DEVICES", command=show_usb_devices_window,
                          width=536, height=44, accent=ACCENT, accent_dim=ACCENT_DIM,
                          font=FONT_MAIN)
btn_devices.pack(pady=(4, 10))



footer = tk.Label(
    panel,
    text="Administrator privileges required  ·  Registry-level control",
    font=FONT_LABEL_SM,
    bg=BG_PANEL,
    fg=TEXT_MUTED
)
footer.pack(side="bottom", pady=(0, 16))

update_status_led()
root.after(50, on_resize)

# Start USB connect/disconnect monitoring (Windows only)
if sys.platform == "win32":
    threading.Thread(target=usb_monitor_thread, daemon=True).start()
    root.after(1000, poll_usb_queue)

root.mainloop()
