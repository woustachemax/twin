import tkinter as tk
import webbrowser

import buddy
import licensing

WIDTH = 420
HEIGHT = 380
MARGIN = 20


class LicenseGate:
    """Blocking modal shown when the trial has run out and no valid license is active.
    Construct it once you have a Tk root and a loaded config, then call .show(message).
    on_unlocked(config) fires once activate() succeeds; the caller is responsible for
    continuing startup from there (this class never proceeds on its own)."""

    def __init__(self, root, config, save_config, on_unlocked, colors):
        self.root = root
        self.config = config
        self.save_config = save_config
        self.on_unlocked = on_unlocked
        self.c = colors
        self.display, self.mono = buddy.pick_fonts()
        self.win = None
        self.canvas = None
        self.entry = None
        self.status = None
        self.button_text = None
        self.checking = False

    def shape(self, tag, x1, y1, x2, y2, r, fill, edge=None, extra=()):
        if edge:
            for item in buddy.rounded_rect_items(self.canvas, x1, y1, x2, y2, r, (tag, f"{tag}_edge") + extra):
                self.canvas.itemconfigure(item, fill=edge)
            x1, y1, x2, y2, r = x1 + 1, y1 + 1, x2 - 1, y2 - 1, r - 1
        for item in buddy.rounded_rect_items(self.canvas, x1, y1, x2, y2, r, (tag, f"{tag}_fill") + extra):
            self.canvas.itemconfigure(item, fill=fill)

    def text(self, x, y, text, size=13, bold=False, color=None, anchor="w", width=None, tags=()):
        font = (self.display, size) + (("bold",) if bold else ())
        return self.canvas.create_text(x, y, text=text, anchor=anchor, font=font, fill=color or self.c["text"],
                                       width=width, tags=tags)

    def show(self, message):
        c = self.c
        win = tk.Toplevel(self.root)
        self.win = win
        win.title("Twin")
        win.configure(bg=c["bg"])
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", lambda: None)  # only Activate/quit-app get out of here

        canvas = tk.Canvas(win, width=WIDTH, height=HEIGHT, bg=c["bg"], highlightthickness=0, bd=0)
        canvas.pack()
        self.canvas = canvas

        dot = buddy.mix(c["bg"], "#FFFFFF", 0.06)
        for x in range(14, WIDTH, 28):
            for y in range(14, HEIGHT, 28):
                canvas.create_oval(x, y, x + 1.6, y + 1.6, fill=dot, outline="")

        self.shape("card", MARGIN, MARGIN, WIDTH - MARGIN, HEIGHT - MARGIN, 20, c["card"], edge=c["line"])
        pad_x = MARGIN + 24
        field_right = WIDTH - pad_x

        self.text(pad_x, MARGIN + 34, "Your trial has ended", size=18, bold=True, color=c["text"], anchor="w")
        message_item = self.text(pad_x, MARGIN + 62, message, size=13, color=c["muted"],
                                 width=field_right - pad_x, anchor="nw")

        entry_top = self.canvas.bbox(message_item)[3] + 16
        self.shape("field", pad_x, entry_top, field_right, entry_top + 42, 14, c["bg2"], edge=c["line"])
        entry = tk.Entry(win, bd=0, highlightthickness=0, relief="flat", bg=c["bg2"], fg=c["text"],
                         insertbackground=c["lime"], font=(self.display, 13))
        canvas.create_window(pad_x + 16, entry_top + 21, anchor="w", window=entry, width=field_right - pad_x - 32)
        entry.focus_set()
        self.entry = entry

        status_top = entry_top + 42 + 12
        self.status = self.text(pad_x, status_top, "", size=11, color=c["pink"],
                                width=field_right - pad_x, anchor="nw")

        button_w, button_h = 160, 44
        button_x1 = (WIDTH - button_w) / 2
        button_top = status_top + 30
        self.shape("button", button_x1, button_top, button_x1 + button_w, button_top + button_h, 20, c["lime"])
        self.button_text = self.text(button_x1 + button_w / 2, button_top + button_h / 2, "Activate", size=14,
                                     bold=True, color=c["face"], anchor="center")
        canvas.tag_bind("button", "<Button-1>", lambda _e: self.do_activate())
        canvas.tag_bind("button", "<Enter>", lambda _e: self.hover_button(True))
        canvas.tag_bind("button", "<Leave>", lambda _e: self.hover_button(False))

        buy_top = button_top + button_h + 22
        self.text(WIDTH / 2, buy_top, "Buy a license →", size=12, color=c["sky"], anchor="center", tags=("buy",))
        canvas.itemconfigure("buy", font=(self.display, 12, "underline"))
        canvas.tag_bind("buy", "<Button-1>", lambda _e: webbrowser.open(licensing.BUY_URL))
        canvas.tag_bind("buy", "<Enter>", lambda _e: canvas.config(cursor="pointinghand"))
        canvas.tag_bind("buy", "<Leave>", lambda _e: canvas.config(cursor=""))

        win.bind("<Return>", lambda _e: self.do_activate())
        win.update_idletasks()
        x = win.winfo_screenwidth() // 2 - WIDTH // 2
        y = win.winfo_screenheight() // 2 - HEIGHT // 2
        win.geometry(f"+{x}+{y}")
        win.grab_set()
        win.lift()

    def hover_button(self, on):
        if not self.checking:
            self.canvas.itemconfigure("button_fill", fill=buddy.mix(self.c["lime"], "#FFFFFF", 0.15) if on else self.c["lime"])
        self.canvas.config(cursor="pointinghand" if on else "")

    def do_activate(self):
        if self.checking:
            return
        self.checking = True
        self.canvas.itemconfigure(self.status, text="Checking...", fill=self.c["muted"])
        self.canvas.update_idletasks()
        try:
            updated = licensing.activate(self.config, self.entry.get())
        except licensing.LicenseError as e:
            self.checking = False
            self.canvas.itemconfigure(self.status, text=str(e), fill=self.c["pink"])
            return
        self.config = updated
        self.save_config(self.config)
        self.win.destroy()
        self.on_unlocked(self.config)
