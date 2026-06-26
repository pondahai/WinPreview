"""
WinPreview - Windows 版預覽程式
整合圖片瀏覽與 PDF 編輯，對標 macOS Preview
拖放累加：可將多個 PDF/圖片疊加成一份多頁文件
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser, simpledialog
from pathlib import Path
import sys

try:
    from PIL import Image, ImageTk, ImageDraw
    import fitz  # PyMuPDF
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "PyMuPDF", "Pillow"])
    from PIL import Image, ImageTk, ImageDraw
    import fitz

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _HAS_DND = True
except ImportError:
    _HAS_DND = False

# ── 常數 ─────────────────────────────────────────────────────────────────────
IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp",
            ".tiff", ".tif", ".webp", ".ico"}
PDF_EXT  = {".pdf"}
ALL_EXTS = IMG_EXTS | PDF_EXT

TOOLBAR_BG   = "#f0f0f0"
SIDEBAR_W    = 130
ANNOT_COLORS = ["#e74c3c", "#e67e22", "#f1c40f", "#2ecc71",
                "#3498db", "#9b59b6", "#1abc9c", "#000000"]

# ── 頁面描述子 ────────────────────────────────────────────────────────────────
# 每個元素是 {"type": "pdf"|"img", "doc": fitz.Document, "page": int}
#                                    或 {"type": "img", "pil": PIL.Image}
class PageEntry:
    """輕量描述子，指向一個 PDF 頁或一張圖片。"""
    __slots__ = ("kind", "doc", "page_idx", "pil", "source_path")

    def __init__(self, *, kind, doc=None, page_idx=0, pil=None, source_path=None):
        self.kind        = kind         # "pdf" | "img"
        self.doc         = doc          # fitz.Document（PDF 用）
        self.page_idx    = page_idx     # PDF 頁碼
        self.pil         = pil          # PIL.Image（圖片用）
        self.source_path = source_path  # 原始檔案路徑


# ── 主應用程式 ────────────────────────────────────────────────────────────────
_Base = TkinterDnD.Tk if _HAS_DND else tk.Tk

class WinPreview(_Base):
    def __init__(self):
        super().__init__()
        self.title("WinPreview")
        self.geometry("1100x750")
        self.minsize(700, 500)
        self.configure(bg="#2b2b2b")

        # ── 核心狀態：頁面清單 ──
        self.pages: list[PageEntry] = []   # 所有頁面，支援累加
        self._open_docs: list[fitz.Document] = []  # 保持開啟的 fitz doc
        self.cur_page   = 0                # 目前頁面索引
        self.zoom       = 1.0
        self.rotation   = 0               # 0/90/180/270
        self.img_offset = [0, 0]
        self._drag_start     = None
        self._canvas_img_id  = None
        self._current_render: Image.Image | None = None

        # ── 標註 ──
        self.annot_tool      = tk.StringVar(value="none")
        self.annot_color     = "#e74c3c"
        self.annot_width     = 2
        self._annot_start    = None
        self._annot_temp_id  = None
        # 每頁獨立標註 {page_index: [(type, coords, color, width, *extra)]}
        self.annot_by_page: dict[int, list] = {}

        self._thumb_imgs: list[ImageTk.PhotoImage] = []

        self._build_ui()
        self._bind_events()
        self._update_status("就緒 — 開啟或拖入檔案（Ctrl+O）")

        if len(sys.argv) > 1:
            self._open_file(Path(sys.argv[1]))

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self._build_menu()
        self._build_toolbar()
        self._build_main_area()
        self._build_statusbar()

    def _build_menu(self):
        mb = tk.Menu(self)
        self.configure(menu=mb)

        fm = tk.Menu(mb, tearoff=False)
        mb.add_cascade(label="檔案", menu=fm)
        fm.add_command(label="開啟…\tCtrl+O",            command=self._cmd_open)
        fm.add_command(label="附加開啟…",                 command=self._cmd_append)
        fm.add_separator()
        fm.add_command(label="儲存\tCtrl+S",              command=self._cmd_save)
        fm.add_command(label="另存新檔…\tCtrl+Shift+S",   command=self._cmd_save_as)
        fm.add_separator()
        fm.add_command(label="匯出為 PDF…",               command=self._cmd_export_pdf)
        fm.add_command(label="匯出目前頁為圖片…",          command=self._cmd_export_image)
        fm.add_separator()
        fm.add_command(label="清空全部頁面",               command=self._cmd_clear_all)
        fm.add_separator()
        fm.add_command(label="結束\tAlt+F4",              command=self.quit)

        em = tk.Menu(mb, tearoff=False)
        mb.add_cascade(label="編輯", menu=em)
        em.add_command(label="清除本頁標註",  command=self._clear_annotations)
        em.add_separator()
        em.add_command(label="旋轉左轉 90°\tCtrl+L", command=lambda: self._rotate(-90))
        em.add_command(label="旋轉右轉 90°\tCtrl+R", command=lambda: self._rotate(90))

        vm = tk.Menu(mb, tearoff=False)
        mb.add_cascade(label="顯示", menu=vm)
        vm.add_command(label="放大\tCtrl++",     command=self._zoom_in)
        vm.add_command(label="縮小\tCtrl+-",     command=self._zoom_out)
        vm.add_command(label="符合視窗\tCtrl+0", command=self._zoom_fit)
        vm.add_command(label="實際大小\tCtrl+1", command=self._zoom_actual)
        vm.add_separator()
        vm.add_command(label="上一頁\tPageUp",   command=self._prev_page)
        vm.add_command(label="下一頁\tPageDown", command=self._next_page)

        tm = tk.Menu(mb, tearoff=False)
        mb.add_cascade(label="工具", menu=tm)
        for label, val in [("選取／捲動", "none"), ("畫筆", "pen"),
                            ("直線", "line"), ("矩形", "rect"),
                            ("橢圓", "oval"), ("文字", "text"),
                            ("螢光筆", "highlight")]:
            tm.add_radiobutton(label=label, variable=self.annot_tool, value=val)

    def _build_toolbar(self):
        tb = tk.Frame(self, bg=TOOLBAR_BG, relief="flat", bd=0, height=44)
        tb.pack(side="top", fill="x")
        tb.pack_propagate(False)

        def btn(text, cmd):
            b = tk.Button(tb, text=text, command=cmd, relief="flat", bd=0,
                          padx=6, pady=4, bg=TOOLBAR_BG,
                          activebackground="#ddd", font=("Segoe UI", 9),
                          cursor="hand2")
            b.pack(side="left", padx=1, pady=4)
            return b

        def sep():
            tk.Frame(tb, bg="#ccc", width=1, height=28).pack(
                side="left", padx=4, pady=8)

        btn("📂 開啟", self._cmd_open)
        btn("➕ 附加", self._cmd_append)
        btn("💾 儲存", self._cmd_save)
        sep()
        btn("⬅", self._prev_page)
        self.page_label = tk.Label(tb, text="", bg=TOOLBAR_BG,
                                   font=("Segoe UI", 9), width=12)
        self.page_label.pack(side="left")
        btn("➡", self._next_page)
        sep()
        btn("🔍+", self._zoom_in)
        btn("🔍-", self._zoom_out)
        btn("⊡ 符合", self._zoom_fit)
        sep()
        btn("↺ 左", lambda: self._rotate(-90))
        btn("↻ 右", lambda: self._rotate(90))
        sep()

        tools = [("✋", "none"), ("✏", "pen"), ("─", "line"),
                 ("▭", "rect"), ("◯", "oval"), ("T", "text"), ("🖊", "highlight")]
        for label, val in tools:
            rb = tk.Radiobutton(tb, text=label, variable=self.annot_tool,
                                value=val, indicator=0, relief="flat",
                                bg=TOOLBAR_BG, activebackground="#ddd",
                                selectcolor="#c8e6c9", padx=5, pady=4,
                                font=("Segoe UI", 9), cursor="hand2")
            rb.pack(side="left", padx=1)
        sep()

        for c in ANNOT_COLORS:
            cb = tk.Canvas(tb, width=20, height=20, bg=c,
                           relief="raised", bd=1, cursor="hand2")
            cb.pack(side="left", padx=1, pady=11)
            cb.bind("<Button-1>", lambda e, col=c: self._set_color(col))
        btn("⚙", self._pick_color)
        sep()
        btn("✕ 清標註", self._clear_annotations)

    def _build_main_area(self):
        pane = tk.PanedWindow(self, orient="horizontal", bg="#2b2b2b",
                              sashwidth=4, sashrelief="flat")
        pane.pack(fill="both", expand=True)

        # 縮圖側欄
        self.sidebar = tk.Frame(pane, bg="#333", width=SIDEBAR_W)
        self.sidebar.pack_propagate(False)

        hdr = tk.Frame(self.sidebar, bg="#333")
        hdr.pack(fill="x")
        tk.Label(hdr, text="頁面", bg="#333", fg="#aaa",
                 font=("Segoe UI", 8)).pack(side="left", padx=6, pady=4)
        tk.Button(hdr, text="✕", command=self._remove_cur_page,
                  bg="#333", fg="#888", relief="flat", bd=0,
                  font=("Segoe UI", 8), cursor="hand2",
                  activebackground="#555").pack(side="right", padx=4)

        self.thumb_canvas = tk.Canvas(self.sidebar, bg="#333",
                                      highlightthickness=0)
        sb_scroll = ttk.Scrollbar(self.sidebar, orient="vertical",
                                  command=self.thumb_canvas.yview)
        self.thumb_canvas.configure(yscrollcommand=sb_scroll.set)
        sb_scroll.pack(side="right", fill="y")
        self.thumb_canvas.pack(fill="both", expand=True)
        self.thumb_frame = tk.Frame(self.thumb_canvas, bg="#333")
        self.thumb_canvas.create_window((0, 0), window=self.thumb_frame,
                                        anchor="nw")
        self.thumb_frame.bind("<Configure>",
            lambda e: self.thumb_canvas.configure(
                scrollregion=self.thumb_canvas.bbox("all")))

        # 主畫布
        view = tk.Frame(pane, bg="#2b2b2b")
        self.canvas = tk.Canvas(view, bg="#2b2b2b",
                                highlightthickness=0, cursor="crosshair")
        hbar = ttk.Scrollbar(view, orient="horizontal", command=self.canvas.xview)
        vbar = ttk.Scrollbar(view, orient="vertical",   command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        hbar.pack(side="bottom", fill="x")
        vbar.pack(side="right",  fill="y")
        self.canvas.pack(fill="both", expand=True)

        pane.add(self.sidebar, minsize=80)
        pane.add(view, minsize=400)
        pane.paneconfigure(self.sidebar, width=SIDEBAR_W)

    def _build_statusbar(self):
        sb = tk.Frame(self, bg="#1e1e1e", height=22)
        sb.pack(side="bottom", fill="x")
        sb.pack_propagate(False)
        self.status_var = tk.StringVar(value="就緒")
        self.zoom_var   = tk.StringVar(value="100%")
        tk.Label(sb, textvariable=self.status_var, bg="#1e1e1e", fg="#ccc",
                 font=("Segoe UI", 8), anchor="w").pack(side="left", padx=8)
        tk.Label(sb, textvariable=self.zoom_var,   bg="#1e1e1e", fg="#8bc34a",
                 font=("Segoe UI", 8), anchor="e").pack(side="right", padx=8)

    # ── 事件綁定 ──────────────────────────────────────────────────────────────
    def _bind_events(self):
        self.bind("<Control-o>",     lambda e: self._cmd_open())
        self.bind("<Control-s>",     lambda e: self._cmd_save())
        self.bind("<Control-S>",     lambda e: self._cmd_save_as())
        self.bind("<Control-plus>",  lambda e: self._zoom_in())
        self.bind("<Control-equal>", lambda e: self._zoom_in())
        self.bind("<Control-minus>", lambda e: self._zoom_out())
        self.bind("<Control-0>",     lambda e: self._zoom_fit())
        self.bind("<Control-1>",     lambda e: self._zoom_actual())
        self.bind("<Control-l>",     lambda e: self._rotate(-90))
        self.bind("<Control-r>",     lambda e: self._rotate(90))
        self.bind("<Prior>",         lambda e: self._prev_page())
        self.bind("<Next>",          lambda e: self._next_page())
        self.bind("<Left>",          lambda e: self._prev_page())
        self.bind("<Right>",         lambda e: self._next_page())

        c = self.canvas
        c.bind("<ButtonPress-1>",      self._on_press)
        c.bind("<B1-Motion>",          self._on_drag)
        c.bind("<ButtonRelease-1>",    self._on_release)
        c.bind("<MouseWheel>",         self._on_scroll)
        c.bind("<Control-MouseWheel>", self._on_ctrl_scroll)
        c.bind("<Configure>",          lambda e: self._render())

        if _HAS_DND:
            # 主視窗與畫布都接受拖放 → 累加
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)
            self.canvas.drop_target_register(DND_FILES)
            self.canvas.dnd_bind("<<Drop>>", self._on_drop)

    def _on_drop(self, event):
        # 用 Tcl 的 splitlist 正確解析路徑（處理含空格的目錄名稱）
        try:
            raw_paths = self.tk.splitlist(event.data)
        except Exception:
            raw_paths = [event.data.strip()]
        added = 0
        for raw in raw_paths:
            p = Path(raw)
            if p.suffix.lower() in ALL_EXTS:
                self._append_file(p)
                added += 1
        if added:
            self._update_status(f"已加入 {added} 個檔案，共 {len(self.pages)} 頁")

    # ── 檔案操作 ──────────────────────────────────────────────────────────────
    def _cmd_open(self):
        paths = filedialog.askopenfilenames(
            title="開啟檔案（可多選）",
            filetypes=[("支援格式", "*.pdf *.jpg *.jpeg *.png *.gif "
                        "*.bmp *.tiff *.tif *.webp"),
                       ("PDF 文件", "*.pdf"),
                       ("圖片", "*.jpg *.jpeg *.png *.gif *.bmp *.tiff *.webp"),
                       ("所有檔案", "*.*")])
        if paths:
            self._clear_all_pages()
            for p in paths:
                self._append_file(Path(p))

    def _cmd_append(self):
        paths = filedialog.askopenfilenames(
            title="附加檔案（可多選）",
            filetypes=[("支援格式", "*.pdf *.jpg *.jpeg *.png *.gif "
                        "*.bmp *.tiff *.tif *.webp"),
                       ("PDF 文件", "*.pdf"),
                       ("圖片", "*.jpg *.jpeg *.png *.gif *.bmp *.tiff *.webp"),
                       ("所有檔案", "*.*")])
        if paths:
            for p in paths:
                self._append_file(Path(p))

    def _open_file(self, path: Path):
        """命令列呼叫用：清空後開啟單一檔案。"""
        self._clear_all_pages()
        self._append_file(path)

    # ── 核心：累加頁面 ────────────────────────────────────────────────────────
    def _append_file(self, path: Path):
        if not path.exists():
            messagebox.showerror("錯誤", f"找不到檔案：{path}")
            return
        ext = path.suffix.lower()
        inserted = 0
        if ext in PDF_EXT:
            try:
                doc = fitz.open(str(path))
                self._open_docs.append(doc)
                for pi in range(len(doc)):
                    self.pages.append(PageEntry(
                        kind="pdf", doc=doc, page_idx=pi, source_path=path))
                    inserted += 1
            except Exception as e:
                messagebox.showerror("無法開啟 PDF", str(e))
                return
        elif ext in IMG_EXTS:
            try:
                img = Image.open(str(path))
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGB")
                else:
                    img = img.copy()
                self.pages.append(PageEntry(
                    kind="img", pil=img, source_path=path))
                inserted += 1
            except Exception as e:
                messagebox.showerror("無法開啟圖片", str(e))
                return
        else:
            messagebox.showwarning("不支援", f"不支援的格式：{ext}")
            return

        # 若是第一批頁面，跳到第一頁；否則跳到新加入的第一頁
        if len(self.pages) == inserted:
            self.cur_page = 0
            self.zoom = 1.0
        else:
            self.cur_page = len(self.pages) - inserted

        self._rebuild_thumbs()
        self._zoom_fit()
        self.title(f"WinPreview — {path.name}  [{len(self.pages)} 頁]")
        self._update_status(f"已加入 {inserted} 頁來自 {path.name}，共 {len(self.pages)} 頁")

    # ── 清空 ─────────────────────────────────────────────────────────────────
    def _clear_all_pages(self):
        for doc in self._open_docs:
            try:
                doc.close()
            except Exception:
                pass
        self._open_docs.clear()
        self.pages.clear()
        self.annot_by_page.clear()
        self.cur_page = 0
        self.img_offset = [0, 0]
        self.canvas.delete("all")
        self._canvas_img_id = None
        self._current_render = None
        for w in self.thumb_frame.winfo_children():
            w.destroy()
        self._thumb_imgs.clear()
        self.page_label.configure(text="")

    def _cmd_clear_all(self):
        if messagebox.askyesno("確認", "清空所有頁面？"):
            self._clear_all_pages()
            self.title("WinPreview")
            self._update_status("就緒")

    def _remove_cur_page(self):
        if not self.pages:
            return
        del self.pages[self.cur_page]
        self.annot_by_page.pop(self.cur_page, None)
        # 重新編號標註
        new_annot = {}
        for k, v in self.annot_by_page.items():
            new_k = k if k < self.cur_page else k - 1
            new_annot[new_k] = v
        self.annot_by_page = new_annot
        self.cur_page = min(self.cur_page, max(0, len(self.pages) - 1))
        self._rebuild_thumbs()
        if self.pages:
            self._render()
        else:
            self.canvas.delete("all")
            self._canvas_img_id = None
            self._update_status("已清空")

    # ── 縮圖 ─────────────────────────────────────────────────────────────────
    def _rebuild_thumbs(self):
        for w in self.thumb_frame.winfo_children():
            w.destroy()
        self._thumb_imgs.clear()

        # 拖曳排序狀態（共用於所有縮圖）
        self._td_src: int | None = None   # 拖曳起始頁索引
        self._td_drop_line = None         # thumb_canvas 上的插入線 id

        for i in range(min(len(self.pages), 300)):
            img = self._render_page(i, zoom=0.12)
            if img is None:
                continue
            tk_img = ImageTk.PhotoImage(img)
            self._thumb_imgs.append(tk_img)

            frame = tk.Frame(self.thumb_frame, bg="#333", cursor="hand2")
            frame.pack(pady=3, padx=4)
            lbl = tk.Label(frame, image=tk_img, bg="#444", relief="flat", bd=1)
            lbl.pack()
            src = self.pages[i].source_path
            name = src.stem[:10] if src else ""
            tk.Label(frame, text=f"{i+1}  {name}", bg="#333", fg="#aaa",
                     font=("Segoe UI", 7)).pack()

            idx = i
            # 點擊選頁（放開且沒有移動才算 click）
            lbl.bind("<ButtonPress-1>",   lambda e, n=idx: self._thumb_press(e, n))
            lbl.bind("<B1-Motion>",        lambda e: self._thumb_motion(e))
            lbl.bind("<ButtonRelease-1>",  lambda e: self._thumb_release(e))
            frame.bind("<ButtonPress-1>",  lambda e, n=idx: self._thumb_press(e, n))
            frame.bind("<B1-Motion>",      lambda e: self._thumb_motion(e))
            frame.bind("<ButtonRelease-1>",lambda e: self._thumb_release(e))

        # thumb_canvas 也要接 motion/release，防止滑鼠滑出縮圖區時失去事件
        self.thumb_canvas.bind("<B1-Motion>",       lambda e: self._thumb_motion(e))
        self.thumb_canvas.bind("<ButtonRelease-1>", lambda e: self._thumb_release(e))
        # DEL 鍵刪除目前頁（需 focus）
        self.thumb_canvas.configure(takefocus=True)
        self.thumb_canvas.bind("<Delete>",          lambda e: self._remove_cur_page())

    def _thumb_press(self, event, idx: int):
        self._td_src      = idx
        self._td_moved    = False
        self._td_press_y  = event.y_root
        self.thumb_canvas.focus_set()   # 讓 DEL 鍵有效

    def _thumb_motion(self, event):
        if self._td_src is None:
            return
        # 超過 4px 才算拖曳（避免誤觸）
        if abs(event.y_root - self._td_press_y) > 4:
            self._td_moved = True
        if not self._td_moved:
            return
        self.thumb_canvas.configure(cursor="sb_v_double_arrow")

        # 計算滑鼠在 thumb_canvas 中的 y（考慮捲動）
        cy = self.thumb_canvas.canvasy(
            event.y_root - self.thumb_canvas.winfo_rooty())
        target = self._thumb_index_at_y(cy)
        self._draw_drop_line(target)

    def _thumb_release(self, event):
        self.thumb_canvas.configure(cursor="")
        if self._td_drop_line:
            self.thumb_canvas.delete(self._td_drop_line)
            self._td_drop_line = None

        if self._td_src is None:
            return
        src = self._td_src
        self._td_src = None

        if not self._td_moved:
            # 純點擊 → 選頁
            self._go_page(src)
            return

        # 計算放下位置
        cy = self.thumb_canvas.canvasy(
            event.y_root - self.thumb_canvas.winfo_rooty())
        dst = self._thumb_index_at_y(cy)

        if dst == src or dst == src + 1:
            return  # 沒有移動

        # 重新排列 pages
        page = self.pages.pop(src)
        annot = self.annot_by_page.pop(src, [])

        # dst 已經是「插入到 dst 之前」，但 pop 後索引需修正
        if dst > src:
            dst -= 1
        self.pages.insert(dst, page)

        # 重建標註映射（舊索引 → 新索引）
        new_annot: dict[int, list] = {}
        for old_i, v in self.annot_by_page.items():
            if old_i == src:
                continue
            if src < old_i <= dst:
                new_annot[old_i - 1] = v
            elif dst <= old_i < src:
                new_annot[old_i + 1] = v
            else:
                new_annot[old_i] = v
        new_annot[dst] = annot
        self.annot_by_page = new_annot

        # 跟隨移動的頁面
        self.cur_page = dst
        self._rebuild_thumbs()
        self._render()
        self._update_status(f"已將第 {src+1} 頁移至第 {dst+1} 頁")

    def _thumb_index_at_y(self, canvas_y: float) -> int:
        """給定 canvas_y，回傳「插入點」索引（0..len(pages)）。"""
        children = self.thumb_frame.winfo_children()
        if not children:
            return 0
        # thumb_frame 相對 thumb_canvas 的偏移（因為 create_window anchor=nw）
        frame_top = self.thumb_frame.winfo_y()
        for i, child in enumerate(children):
            cy_mid = frame_top + child.winfo_y() + child.winfo_height() // 2
            if canvas_y < cy_mid:
                return i
        return len(children)

    def _draw_drop_line(self, insert_at: int):
        if self._td_drop_line:
            self.thumb_canvas.delete(self._td_drop_line)
        children = self.thumb_frame.winfo_children()
        frame_top = self.thumb_frame.winfo_y()
        w = self.thumb_canvas.winfo_width()

        if insert_at == 0:
            y = frame_top + (children[0].winfo_y() if children else 0)
        elif insert_at >= len(children):
            last = children[-1]
            y = frame_top + last.winfo_y() + last.winfo_height()
        else:
            child = children[insert_at]
            y = frame_top + child.winfo_y()

        self._td_drop_line = self.thumb_canvas.create_line(
            4, y, w - 4, y, fill="#4fc3f7", width=2)

    def _highlight_thumb(self, idx):
        children = self.thumb_frame.winfo_children()
        for i, child in enumerate(children):
            child.configure(bg="#555" if i == idx else "#333")

    # ── 頁面渲染 ──────────────────────────────────────────────────────────────
    def _render_page(self, page_index: int, zoom: float = None) -> Image.Image | None:
        if page_index < 0 or page_index >= len(self.pages):
            return None
        z = zoom if zoom is not None else self.zoom
        entry = self.pages[page_index]
        if entry.kind == "pdf":
            page = entry.doc[entry.page_idx]
            mat  = fitz.Matrix(z * 2, z * 2).prerotate(self.rotation)
            pix  = page.get_pixmap(matrix=mat, alpha=False)
            return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        else:  # img
            img = entry.pil.copy()
            if self.rotation:
                img = img.rotate(-self.rotation, expand=True)
            w = max(1, int(img.width  * z))
            h = max(1, int(img.height * z))
            return img.resize((w, h), Image.LANCZOS)

    def _render(self):
        if not self.pages:
            return
        img = self._render_page(self.cur_page)
        if img is None:
            return
        # 確保 RGB
        if img.mode != "RGB":
            img = img.convert("RGB")
        self._current_render = img.copy()

        draw = ImageDraw.Draw(img)
        self._draw_annotations(draw, img, self.cur_page)

        tk_img = ImageTk.PhotoImage(img)
        self.tk_image = tk_img  # 保持參考，防止 GC

        cw = self.canvas.winfo_width()  or 800
        ch = self.canvas.winfo_height() or 600
        x = max(cw // 2, img.width  // 2 + 20)
        y = max(ch // 2, img.height // 2 + 20)

        self.canvas.configure(scrollregion=(
            0, 0, max(cw, img.width + 40), max(ch, img.height + 40)))
        if self._canvas_img_id:
            self.canvas.delete(self._canvas_img_id)
        self._canvas_img_id = self.canvas.create_image(
            x + self.img_offset[0], y + self.img_offset[1],
            anchor="center", image=self.tk_image)

        total = len(self.pages)
        self.page_label.configure(
            text=f"{self.cur_page + 1} / {total}" if total > 0 else "")
        self.zoom_var.set(f"{int(self.zoom * 100)}%")
        self._highlight_thumb(self.cur_page)

    # ── 標註繪製 ──────────────────────────────────────────────────────────────
    @property
    def annotations(self) -> list:
        return self.annot_by_page.setdefault(self.cur_page, [])

    def _draw_annotations(self, draw, img, page_idx):
        for annot in self.annot_by_page.get(page_idx, []):
            t, coords, color, width, *extra = annot
            if t == "pen":
                for j in range(len(coords) - 1):
                    draw.line([coords[j], coords[j+1]], fill=color, width=width)
            elif t == "line":
                draw.line(coords, fill=color, width=width)
            elif t == "rect":
                draw.rectangle(coords, outline=color, width=width)
            elif t == "oval":
                draw.ellipse(coords, outline=color, width=width)
            elif t == "highlight":
                overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
                od = ImageDraw.Draw(overlay)
                r, g, b = self._hex_to_rgb(color)
                od.rectangle(coords, fill=(r, g, b, 100))
                merged = Image.alpha_composite(
                    img.convert("RGBA"), overlay).convert("RGB")
                img.paste(merged)
            elif t == "text":
                txt = extra[0] if extra else ""
                draw.text(coords[0], txt, fill=color)

    # ── 標註事件 ──────────────────────────────────────────────────────────────
    def _canvas_to_img(self, cx, cy):
        if self._canvas_img_id is None or self._current_render is None:
            return cx, cy
        x0, y0 = self.canvas.coords(self._canvas_img_id)
        iw, ih  = self._current_render.size
        return int(cx - (x0 - iw // 2)), int(cy - (y0 - ih // 2))

    def _on_press(self, event):
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        tool = self.annot_tool.get()
        if tool == "none":
            self._drag_start = (cx, cy)
            self.canvas.configure(cursor="fleur")
            return
        px, py = self._canvas_to_img(cx, cy)
        self._annot_start = (px, py)
        if tool == "pen":
            self._pen_points = [(px, py)]
        elif tool == "text":
            txt = simpledialog.askstring("文字標註", "輸入文字：")
            if txt:
                self.annotations.append(
                    ("text", [(px, py)], self.annot_color,
                     self.annot_width, txt))
                self._render()

    def _on_drag(self, event):
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        tool = self.annot_tool.get()
        if tool == "none":
            if self._drag_start:
                self.img_offset[0] += cx - self._drag_start[0]
                self.img_offset[1] += cy - self._drag_start[1]
                self._drag_start = (cx, cy)
                self._render()
            return
        if self._annot_start is None:
            return
        px, py = self._canvas_to_img(cx, cy)
        if tool == "pen":
            self._pen_points.append((px, py))
            self._render()
        elif tool in ("line", "rect", "oval", "highlight"):
            if self._annot_temp_id:
                self.canvas.delete(self._annot_temp_id)
            sx, sy = self._annot_start
            if self._canvas_img_id and self._current_render:
                x0, y0 = self.canvas.coords(self._canvas_img_id)
                iw, ih = self._current_render.size
                ox, oy = x0 - iw // 2, y0 - ih // 2
                c1x, c1y = sx + ox, sy + oy
                c2x, c2y = px + ox, py + oy
            else:
                c1x, c1y, c2x, c2y = sx, sy, cx, cy
            kw = dict(outline=self.annot_color, width=self.annot_width, dash=(4, 2))
            if tool in ("rect", "highlight"):
                self._annot_temp_id = self.canvas.create_rectangle(
                    c1x, c1y, c2x, c2y, **kw)
            elif tool == "oval":
                self._annot_temp_id = self.canvas.create_oval(
                    c1x, c1y, c2x, c2y, **kw)
            elif tool == "line":
                self._annot_temp_id = self.canvas.create_line(
                    c1x, c1y, c2x, c2y, fill=self.annot_color,
                    width=self.annot_width, dash=(4, 2))

    def _on_release(self, event):
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        tool = self.annot_tool.get()
        self.canvas.configure(cursor="crosshair")
        self._drag_start = None
        if tool == "none" or self._annot_start is None:
            self._annot_start = None
            return
        if self._annot_temp_id:
            self.canvas.delete(self._annot_temp_id)
            self._annot_temp_id = None
        px, py = self._canvas_to_img(cx, cy)
        sx, sy = self._annot_start
        if tool == "pen":
            if len(self._pen_points) > 1:
                self.annotations.append(
                    ("pen", self._pen_points, self.annot_color, self.annot_width))
        elif tool in ("line", "rect", "oval", "highlight"):
            self.annotations.append(
                (tool, [(sx, sy), (px, py)], self.annot_color, self.annot_width))
        self._annot_start = None
        self._render()

    def _on_scroll(self, event):
        if len(self.pages) > 1:
            self._next_page() if event.delta < 0 else self._prev_page()

    def _on_ctrl_scroll(self, event):
        self._zoom_in() if event.delta > 0 else self._zoom_out()

    # ── 縮放 / 旋轉 / 頁面 ───────────────────────────────────────────────────
    def _zoom_in(self):
        self.zoom = min(self.zoom * 1.25, 20.0)
        self.img_offset = [0, 0]
        self._render()

    def _zoom_out(self):
        self.zoom = max(self.zoom / 1.25, 0.05)
        self.img_offset = [0, 0]
        self._render()

    def _zoom_fit(self):
        self.update_idletasks()
        cw = self.canvas.winfo_width()  or 800
        ch = self.canvas.winfo_height() or 600
        img = self._render_page(self.cur_page, zoom=1.0)
        if img is None:
            return
        iw, ih = img.size
        self.zoom = max(0.05, min((cw - 40) / max(iw, 1),
                                  (ch - 40) / max(ih, 1)))
        self.img_offset = [0, 0]
        self._render()

    def _zoom_actual(self):
        self.zoom = 1.0
        self.img_offset = [0, 0]
        self._render()

    def _rotate(self, deg):
        self.rotation = (self.rotation + deg) % 360
        self._render()

    def _go_page(self, idx):
        if 0 <= idx < len(self.pages):
            self.cur_page = idx
            self.img_offset = [0, 0]
            self._render()

    def _prev_page(self): self._go_page(self.cur_page - 1)
    def _next_page(self): self._go_page(self.cur_page + 1)

    # ── 標註工具設定 ──────────────────────────────────────────────────────────
    def _set_color(self, color):
        self.annot_color = color

    def _pick_color(self):
        c = colorchooser.askcolor(color=self.annot_color, title="選擇顏色")
        if c and c[1]:
            self.annot_color = c[1]

    def _clear_annotations(self):
        self.annot_by_page.pop(self.cur_page, None)
        self._render()

    # ── 儲存 / 匯出 ───────────────────────────────────────────────────────────
    def _cmd_save(self):
        if not self.pages:
            messagebox.showinfo("提示", "目前沒有任何頁面")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF（保留所有頁面）", "*.pdf"),
                       ("PNG（目前頁）", "*.png"),
                       ("JPEG（目前頁）", "*.jpg")],
            title="儲存")
        if not path:
            return
        p = Path(path)
        if p.suffix.lower() == ".pdf":
            self._export_all_as_pdf(p)
        else:
            self._save_cur_page_as_image(p)

    def _cmd_save_as(self):
        self._cmd_save()

    def _cmd_export_pdf(self):
        if not self.pages:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
            title="匯出所有頁面為 PDF")
        if path:
            self._export_all_as_pdf(Path(path))

    def _cmd_export_image(self):
        if not self.pages:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("BMP", "*.bmp")],
            title="匯出目前頁為圖片")
        if path:
            self._save_cur_page_as_image(Path(path))

    def _export_all_as_pdf(self, out_path: Path):
        try:
            doc = fitz.open()
            for i, entry in enumerate(self.pages):
                if entry.kind == "pdf":
                    src_page = entry.doc[entry.page_idx]
                    new_page = doc.new_page(width=src_page.rect.width,
                                            height=src_page.rect.height)
                    new_page.show_pdf_page(new_page.rect, entry.doc, entry.page_idx)
                else:
                    img = entry.pil.copy()
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    buf = __import__("io").BytesIO()
                    img.save(buf, format="PNG")
                    new_page = doc.new_page(width=img.width, height=img.height)
                    new_page.insert_image(new_page.rect, stream=buf.getvalue())
                # 疊加標註（簡易版：寫入 fitz 原生標註）
                for annot in self.annot_by_page.get(i, []):
                    t, coords, color, width, *extra = annot
                    r, g, b = [c/255 for c in self._hex_to_rgb(color)]
                    try:
                        if t == "rect":
                            x0,y0=coords[0]; x1,y1=coords[1]
                            sc = new_page.rect.width / (self._render_page(i, zoom=1.0) or type('x',(),{'size':(new_page.rect.width*2,1)})()).size[0]
                            a = new_page.add_rect_annot(
                                fitz.Rect(x0*sc,y0*sc,x1*sc,y1*sc))
                            a.set_colors(stroke=(r,g,b)); a.update()
                    except Exception:
                        pass
            doc.save(str(out_path))
            doc.close()
            self._update_status(f"已匯出 PDF：{out_path}（{len(self.pages)} 頁）")
        except Exception as e:
            messagebox.showerror("匯出失敗", str(e))

    def _save_cur_page_as_image(self, path: Path):
        try:
            img = self._render_page(self.cur_page, zoom=1.0)
            if img is None:
                return
            if img.mode != "RGB":
                img = img.convert("RGB")
            draw = ImageDraw.Draw(img)
            self._draw_annotations(draw, img, self.cur_page)
            img.save(str(path))
            self._update_status(f"已儲存：{path}")
        except Exception as e:
            messagebox.showerror("儲存失敗", str(e))

    # ── 工具函式 ──────────────────────────────────────────────────────────────
    def _hex_to_rgb(self, hex_color: str):
        h = hex_color.lstrip("#")
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    def _update_status(self, msg: str):
        self.status_var.set(msg)


# ── 啟動 ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = WinPreview()
    app.mainloop()
