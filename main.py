"""
WinPreview - Windows 版預覽程式
整合圖片瀏覽與 PDF 編輯，對標 macOS Preview
拖放累加：可將多個 PDF/圖片疊加成一份多頁文件
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser, simpledialog
from pathlib import Path
import sys

# 是否為 PyInstaller 等打包後的凍結環境
_FROZEN = getattr(sys, "frozen", False)

try:
    from PIL import Image, ImageTk, ImageDraw, ImageFont
    import pypdfium2 as pdfium  # PDF 渲染（Apache/BSD，取代 PyMuPDF）
except ImportError as _e:
    if _FROZEN:
        # 打包後 sys.executable 就是本程式；切勿用它自我重啟安裝套件，
        # 否則會無限自我複製（程序炸彈）。缺元件就直接報錯結束。
        import tkinter as _tk
        from tkinter import messagebox as _mb
        _root = _tk.Tk(); _root.withdraw()
        _mb.showerror("WinPreview 無法啟動",
                      f"缺少必要元件：{_e}\n此為打包檔損毀，請重新下載。")
        sys.exit(1)
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install",
                           "pypdfium2", "Pillow"])
    from PIL import Image, ImageTk, ImageDraw, ImageFont
    import pypdfium2 as pdfium

import io

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
# 每個元素是 {"type": "pdf"|"img", "doc": pdfium.PdfDocument, "page": int}
#                                    或 {"type": "img", "pil": PIL.Image}
class PageEntry:
    """輕量描述子，指向一個 PDF 頁或一張圖片。"""
    __slots__ = ("kind", "doc", "page_idx", "pil", "source_path")

    def __init__(self, *, kind, doc=None, page_idx=0, pil=None, source_path=None):
        self.kind        = kind         # "pdf" | "img"
        self.doc         = doc          # pdfium.PdfDocument（PDF 用）
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
        self._open_docs: list = []         # 保持開啟的 pdfium PdfDocument
        self._size_cache: dict = {}        # (id(doc), page_idx) → (W0, H0) 基底尺寸
        self.cur_page   = 0                # 目前頁面索引
        self.zoom       = 1.0
        self.rotation   = 0               # 0/90/180/270
        self.img_offset = [0, 0]
        self._drag_start     = None
        self._canvas_img_id  = None
        self._current_render: Image.Image | None = None

        # ── 檢視模式 ──
        self.view_mode = tk.StringVar(value="single")   # single | continuous
        self._cont_gap    = 16             # 連續模式頁間距（畫布像素）
        self._cont_margin = 20
        self._cont_layout: list[dict] = [] # 每頁版面 {idx,x,y,w,h}（內容座標）
        self._cont_items:  dict[int, tuple] = {}  # idx -> (image_id, PhotoImage)
        self._cont_frames: list[int] = []  # 頁框矩形 id
        self._cont_content = (0, 0)        # (寬, 高)
        self._cont_pending = False         # 重繪可見頁去抖動旗標
        self._annot_page  = None           # 標註中目標頁（連續模式）

        # ── 標註 ──
        # 座標一律存「基底空間」：未旋轉、zoom=1.0 的頁面像素座標，
        # 因此標註不受縮放/旋轉影響，且可正確匯出。
        self._selected_annot   = None   # 選取中的標註索引（select 工具）
        self.annot_tool        = tk.StringVar(value="none")
        self.annot_tool.trace_add("write", self._on_tool_change)
        self.annot_color       = "#e74c3c"
        self.annot_width       = 2      # 螢幕像素寬（繪製當下）
        self._annot_start      = None   # 基底座標
        self._annot_start_cvs  = None   # 畫布座標（橡皮筋預覽用）
        self._annot_temp_id    = None
        # 拖曳搬移選取標註用
        self._move_start_base  = None   # 按下時的基底座標
        self._move_orig_coords = None   # 被搬移標註的原始座標（供計算與復原）
        self._moving           = False
        # 每頁獨立標註 {page_index: [(type, coords_base, color, width_base, *extra)]}
        self.annot_by_page: dict[int, list] = {}
        self._font_cache: dict[int, object] = {}
        # 復原堆疊：記錄標註動作以供 Ctrl+Z 回退
        #   ("add",   page)            → 移除該頁最後一筆標註
        #   ("clear", page, old_list)  → 還原整頁標註
        self._undo_stack: list = []

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
        vm.add_radiobutton(label="單頁顯示", variable=self.view_mode,
                           value="single", command=self._on_view_mode_change)
        vm.add_radiobutton(label="連續顯示", variable=self.view_mode,
                           value="continuous", command=self._on_view_mode_change)
        vm.add_separator()
        vm.add_command(label="放大\tCtrl++",     command=self._zoom_in)
        vm.add_command(label="縮小\tCtrl+-",     command=self._zoom_out)
        vm.add_command(label="符合視窗\tCtrl+0", command=self._zoom_fit)
        vm.add_command(label="實際大小\tCtrl+1", command=self._zoom_actual)
        vm.add_separator()
        vm.add_command(label="上一頁\tPageUp",   command=self._prev_page)
        vm.add_command(label="下一頁\tPageDown", command=self._next_page)

        tm = tk.Menu(mb, tearoff=False)
        mb.add_cascade(label="工具", menu=tm)
        for label, val in [("平移／捲動", "none"), ("選取標註", "select"),
                            ("畫筆", "pen"),
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
        # 檢視模式：單頁 / 連續
        for label, val in [("▤ 單頁", "single"), ("☰ 連續", "continuous")]:
            tk.Radiobutton(tb, text=label, variable=self.view_mode, value=val,
                           indicator=0, relief="flat", bg=TOOLBAR_BG,
                           activebackground="#ddd", selectcolor="#c8e6c9",
                           padx=5, pady=4, font=("Segoe UI", 9), cursor="hand2",
                           command=self._on_view_mode_change).pack(side="left", padx=1)
        sep()
        btn("↺ 左", lambda: self._rotate(-90))
        btn("↻ 右", lambda: self._rotate(90))
        sep()

        tools = [("✋", "none"), ("▣", "select"), ("✏", "pen"), ("─", "line"),
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
        self.bind("<Control-z>",     lambda e: self._undo())
        self.bind("<Control-Z>",     lambda e: self._undo())
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
        c.configure(takefocus=True)
        c.bind("<Delete>",             lambda e: self._delete_selected())
        c.bind("<BackSpace>",          lambda e: self._delete_selected())

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
                doc = pdfium.PdfDocument(str(path))
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
        self._size_cache.clear()
        self.pages.clear()
        self.annot_by_page.clear()
        self._undo_stack.clear()
        self._selected_annot = None
        self.cur_page = 0
        self.img_offset = [0, 0]
        self.canvas.delete("all")
        self._canvas_img_id = None
        self._current_render = None
        self._cont_items.clear()
        self._cont_frames.clear()
        self._cont_layout = []
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
        self._undo_stack.clear()   # 頁碼重編，舊復原紀錄會失準
        self._selected_annot = None
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

        # 縮圖統一框進固定方框，避免寬圖/長頁撐爆側欄版面
        thumb_w = max(40, SIDEBAR_W - 34)   # 目標寬度（扣掉捲軸與邊距）
        thumb_h = 150                        # 目標高度上限
        for i in range(min(len(self.pages), 300)):
            bw, bh = self._base_size(i)
            if self.rotation in (90, 270):
                bw, bh = bh, bw
            # 取寬、高兩個限制中較小的縮放，確保兩個方向都不超框
            z = min(thumb_w / max(bw, 1), thumb_h / max(bh, 1))
            img = self._render_page(i, zoom=z)
            if img is None:
                continue
            # 保險：若仍超出（捨入誤差）再夾一次
            if img.width > thumb_w or img.height > thumb_h:
                img.thumbnail((thumb_w, thumb_h), Image.LANCZOS)
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
        self._undo_stack.clear()   # 頁碼重編，舊復原紀錄會失準
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
        self._scroll_thumb_into_view(idx)

    def _scroll_thumb_into_view(self, idx):
        """讓側欄自動捲到目前頁的縮圖（主檢視 → 側欄連動）。"""
        children = self.thumb_frame.winfo_children()
        if not (0 <= idx < len(children)):
            return
        # 拖曳排序進行中就不要搶捲動
        if getattr(self, "_td_src", None) is not None:
            return
        self.thumb_canvas.update_idletasks()
        child  = children[idx]
        total  = self.thumb_frame.winfo_height()
        if total <= 0:
            return
        top    = child.winfo_y()
        bottom = top + child.winfo_height()
        view_h = self.thumb_canvas.winfo_height()
        y0     = self.thumb_canvas.canvasy(0)          # 目前可視頂端
        y1     = y0 + view_h                            # 目前可視底端
        if top < y0:                                   # 在上方 → 對齊頂端
            self.thumb_canvas.yview_moveto(top / total)
        elif bottom > y1:                              # 在下方 → 對齊底端
            self.thumb_canvas.yview_moveto(max(0, (bottom - view_h)) / total)

    # ── 頁面渲染 ──────────────────────────────────────────────────────────────
    def _render_page(self, page_index: int, zoom: float = None) -> Image.Image | None:
        if page_index < 0 or page_index >= len(self.pages):
            return None
        z = zoom if zoom is not None else self.zoom
        entry = self.pages[page_index]
        if entry.kind == "pdf":
            page = entry.doc[entry.page_idx]
            try:
                # scale = 點數→像素倍率；維持「zoom=1.0 時 2x 點數」的基底慣例
                bitmap = page.render(scale=z * 2, rotation=self.rotation)
                img = bitmap.to_pil()
                return img.convert("RGB") if img.mode != "RGB" else img
            finally:
                page.close()
        else:  # img
            img = entry.pil.copy()
            if self.rotation:
                img = img.rotate(-self.rotation, expand=True)
            w = max(1, int(img.width  * z))
            h = max(1, int(img.height * z))
            return img.resize((w, h), Image.LANCZOS)

    def _render(self):
        if self.view_mode.get() == "continuous":
            self._render_continuous()
        else:
            self._render_single()

    def _render_single(self):
        if not self.pages:
            return
        # 清掉連續模式殘留
        if self._cont_items or self._cont_frames:
            self.canvas.delete("all")
            self._cont_items.clear()
            self._cont_frames.clear()
            self._canvas_img_id = None
        img = self._render_page(self.cur_page)
        if img is None:
            return
        if img.mode != "RGB":
            img = img.convert("RGB")
        self._current_render = img.copy()

        draw = ImageDraw.Draw(img)
        W0, H0 = self._base_size(self.cur_page)
        to_disp = lambda x, y: self._base_to_display(x, y, W0, H0)
        annots = self.annot_by_page.get(self.cur_page, [])
        self._draw_annotations(draw, img, annots, to_disp=to_disp, scale=self.zoom)

        # 選取框（僅 select 工具、且選取索引有效時繪製，不會匯出）
        if (self.annot_tool.get() == "select"
                and self._selected_annot is not None
                and 0 <= self._selected_annot < len(annots)):
            x0, y0, x1, y1 = self._annot_bbox(annots[self._selected_annot])
            # 轉換四角再取範圍（含旋轉時軸向會交換）
            pts = [to_disp(x0, y0), to_disp(x1, y0), to_disp(x1, y1), to_disp(x0, y1)]
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            m = 4  # 外擴邊距（顯示像素）
            draw.rectangle([min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m],
                           outline="#00a8ff", width=2)

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

    # ── 連續顯示模式 ──────────────────────────────────────────────────────────
    def _disp_size(self, page_idx: int):
        """該頁在目前縮放/旋轉下的顯示尺寸（畫布像素）。"""
        W0, H0 = self._base_size(page_idx)
        if self.rotation in (90, 270):
            W0, H0 = H0, W0
        return max(1, int(W0 * self.zoom)), max(1, int(H0 * self.zoom))

    def _cont_build_layout(self):
        """只計算幾何（不渲染），決定每頁在畫布上的位置。"""
        self._cont_layout = []
        m, gap = self._cont_margin, self._cont_gap
        sizes = [self._disp_size(i) for i in range(len(self.pages))]
        content_w = (max((w for w, _ in sizes), default=0)) + 2 * m
        cw = self.canvas.winfo_width() or 800
        content_w = max(content_w, cw)
        y = m
        for i, (w, h) in enumerate(sizes):
            x = (content_w - w) // 2
            self._cont_layout.append({"idx": i, "x": x, "y": y, "w": w, "h": h})
            y += h + gap
        content_h = y - gap + m if sizes else 0
        self._cont_content = (content_w, max(content_h, 1))

    def _render_continuous(self):
        if not self.pages:
            return
        self.canvas.delete("all")
        self._cont_items.clear()
        self._cont_frames.clear()
        self._canvas_img_id = None
        self._current_render = None

        self._cont_build_layout()
        cw, ch = self._cont_content
        self.canvas.configure(scrollregion=(0, 0, cw, ch))

        # 先畫每頁的頁框（佔位，捲到才填影像）
        for e in self._cont_layout:
            fid = self.canvas.create_rectangle(
                e["x"], e["y"], e["x"] + e["w"], e["y"] + e["h"],
                outline="#555", width=1, fill="#3a3a3a")
            self._cont_frames.append(fid)

        self._cont_refresh_visible()
        self.zoom_var.set(f"{int(self.zoom * 100)}%")
        self._cont_update_curpage()

    def _cont_visible_range(self):
        top = self.canvas.canvasy(0)
        vh  = self.canvas.winfo_height() or 600
        bot = top + vh
        buf = vh                      # 預先渲染上下各一個視窗高度
        return top - buf, bot + buf

    def _cont_refresh_visible(self):
        if self.view_mode.get() != "continuous" or not self._cont_layout:
            return
        lo, hi = self._cont_visible_range()
        want = set()
        for e in self._cont_layout:
            if e["y"] + e["h"] >= lo and e["y"] <= hi:
                want.add(e["idx"])
                if e["idx"] not in self._cont_items:
                    self._cont_render_one(e)
        # 釋放離開視窗太遠的頁，控制記憶體
        for idx in list(self._cont_items):
            if idx not in want:
                img_id, _ = self._cont_items.pop(idx)
                self.canvas.delete(img_id)

    def _cont_render_one(self, e: dict):
        img = self._render_page(e["idx"])
        if img is None:
            return
        if img.mode != "RGB":
            img = img.convert("RGB")
        draw = ImageDraw.Draw(img)
        W0, H0 = self._base_size(e["idx"])
        self._draw_annotations(
            draw, img, self.annot_by_page.get(e["idx"], []),
            to_disp=lambda x, y: self._base_to_display(x, y, W0, H0),
            scale=self.zoom)
        photo = ImageTk.PhotoImage(img)
        img_id = self.canvas.create_image(e["x"], e["y"], anchor="nw", image=photo)
        self._cont_items[e["idx"]] = (img_id, photo)

    def _cont_update_curpage(self):
        """依捲動位置推算目前頁（視窗中央落在哪一頁）。"""
        if not self._cont_layout:
            return
        mid = self.canvas.canvasy(0) + (self.canvas.winfo_height() or 600) / 2
        best, bestd = self.cur_page, None
        for e in self._cont_layout:
            c = e["y"] + e["h"] / 2
            d = abs(c - mid)
            if bestd is None or d < bestd:
                bestd, best = d, e["idx"]
        if best != self.cur_page:
            self.cur_page = best
        total = len(self.pages)
        self.page_label.configure(text=f"{self.cur_page + 1} / {total}")
        self._highlight_thumb(self.cur_page)

    def _cont_redraw_page(self, idx: int):
        """重畫單一頁（標註變動後）。"""
        if idx in self._cont_items:
            img_id, _ = self._cont_items.pop(idx)
            self.canvas.delete(img_id)
        for e in self._cont_layout:
            if e["idx"] == idx:
                self._cont_render_one(e)
                break

    def _on_view_mode_change(self):
        self.img_offset = [0, 0]
        if self.view_mode.get() == "continuous":
            # 進入連續模式：符合頁寬，再捲到目前頁
            self._zoom_fit()
            self._cont_scroll_to_page(self.cur_page)
        else:
            self._render()

    def _cont_scroll_to_page(self, idx):
        for e in self._cont_layout:
            if e["idx"] == idx:
                _, ch = self._cont_content
                self.canvas.yview_moveto(max(0, e["y"] - self._cont_margin) / ch)
                self._cont_refresh_visible()
                break

    # ── 座標系統 ──────────────────────────────────────────────────────────────
    # 「基底空間」= 未旋轉、zoom=1.0 的頁面像素。PDF 在 zoom=1.0 時以 2x 點數
    # 渲染，故基底寬高 = 點數 x 2；圖片則為原始像素。
    def _base_size(self, page_idx: int):
        if page_idx < 0 or page_idx >= len(self.pages):
            return (1, 1)
        entry = self.pages[page_idx]
        if entry.kind == "pdf":
            key = (id(entry.doc), entry.page_idx)
            cached = self._size_cache.get(key)
            if cached is not None:
                return cached
            page = entry.doc[entry.page_idx]
            try:
                w, h = page.get_size()  # 點數
            finally:
                page.close()
            size = (max(1, int(round(w * 2))), max(1, int(round(h * 2))))
            self._size_cache[key] = size
            return size
        return (entry.pil.width, entry.pil.height)

    def _base_to_display(self, x, y, W0, H0):
        """基底座標 → 顯示影像像素（含目前旋轉與縮放）。"""
        r = self.rotation
        if   r == 90:  bx, by = H0 - y, x
        elif r == 180: bx, by = W0 - x, H0 - y
        elif r == 270: bx, by = y, W0 - x
        else:          bx, by = x, y
        return bx * self.zoom, by * self.zoom

    def _display_to_base(self, dx, dy, W0, H0):
        """顯示影像像素 → 基底座標（_base_to_display 的反運算）。"""
        x, y = dx / self.zoom, dy / self.zoom
        r = self.rotation
        if   r == 90:  return y, H0 - x
        elif r == 180: return W0 - x, H0 - y
        elif r == 270: return W0 - y, x
        return x, y

    def _canvas_to_base(self, cx, cy):
        if self._canvas_img_id is None or self._current_render is None:
            return cx, cy
        x0, y0 = self.canvas.coords(self._canvas_img_id)
        iw, ih = self._current_render.size
        dx = cx - (x0 - iw / 2)
        dy = cy - (y0 - ih / 2)
        W0, H0 = self._base_size(self.cur_page)
        return self._display_to_base(dx, dy, W0, H0)

    def _cont_page_at(self, cx, cy):
        """連續模式：找出 (cx,cy) 落在哪一頁的版面，回傳該 layout entry。"""
        for e in self._cont_layout:
            if e["x"] <= cx <= e["x"] + e["w"] and e["y"] <= cy <= e["y"] + e["h"]:
                return e
        return None

    def _canvas_to_base_for(self, idx, cx, cy):
        """連續模式：把畫布座標換成指定頁的基底座標。"""
        e = next((e for e in self._cont_layout if e["idx"] == idx), None)
        if e is None:
            return (cx, cy)
        W0, H0 = self._base_size(idx)
        return self._display_to_base(cx - e["x"], cy - e["y"], W0, H0)

    def _font(self, size: int):
        size = max(6, int(size))
        if size in self._font_cache:
            return self._font_cache[size]
        font = None
        for path in (r"C:\Windows\Fonts\msjh.ttc",   # 微軟正黑（支援中文）
                     r"C:\Windows\Fonts\simsun.ttc",
                     r"C:\Windows\Fonts\arial.ttf"):
            try:
                font = ImageFont.truetype(path, size)
                break
            except Exception:
                continue
        if font is None:
            font = ImageFont.load_default()
        self._font_cache[size] = font
        return font

    # ── 標註繪製 ──────────────────────────────────────────────────────────────
    @property
    def annotations(self) -> list:
        return self.annot_by_page.setdefault(self.cur_page, [])

    def _draw_annotations(self, draw, img, annots, to_disp, scale):
        """以 to_disp(基底→目標影像像素) 與 scale(寬度比例) 繪製標註。"""
        for annot in annots:
            t, coords, color, wbase, *extra = annot
            w = max(1, int(round(wbase * scale)))
            pts = [to_disp(x, y) for (x, y) in coords]
            if t == "pen":
                if len(pts) > 1:
                    draw.line(pts, fill=color, width=w, joint="curve")
            elif t == "line":
                if len(pts) >= 2:
                    draw.line(pts, fill=color, width=w)
            elif t in ("rect", "oval", "highlight"):
                xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                box = [min(xs), min(ys), max(xs), max(ys)]
                if t == "rect":
                    draw.rectangle(box, outline=color, width=w)
                elif t == "oval":
                    draw.ellipse(box, outline=color, width=w)
                else:  # highlight：半透明填色
                    r, g, b = self._hex_to_rgb(color)
                    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
                    ImageDraw.Draw(overlay).rectangle(box, fill=(r, g, b, 110))
                    merged = Image.alpha_composite(img.convert("RGBA"), overlay)
                    img.paste(merged.convert(img.mode))
            elif t == "text":
                txt   = extra[0] if extra else ""
                tbase = extra[1] if len(extra) > 1 else 16
                draw.text(pts[0], txt, fill=color,
                          font=self._font(round(tbase * scale)))

    # ── 標註事件 ──────────────────────────────────────────────────────────────
    def _annot_target_list(self):
        """目前標註要寫入的清單（依模式選頁）。"""
        idx = self._annot_page if self._annot_page is not None else self.cur_page
        return self.annot_by_page.setdefault(idx, [])

    def _map_to_target(self, cx, cy):
        """畫布座標 → 目標頁基底座標（單頁/連續通用）。"""
        if self.view_mode.get() == "continuous":
            return self._canvas_to_base_for(self._annot_page, cx, cy)
        return self._canvas_to_base(cx, cy)

    def _redraw_after_annot(self):
        if self.view_mode.get() == "continuous":
            self._cont_redraw_page(
                self._annot_page if self._annot_page is not None else self.cur_page)
        else:
            self._render()

    def _on_press(self, event):
        self.canvas.focus_set()   # 讓 Delete/BackSpace 鍵有效
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        tool = self.annot_tool.get()
        cont = self.view_mode.get() == "continuous"
        if tool == "none":
            self._drag_start = (cx, cy)
            self.canvas.configure(cursor="fleur")
            if cont:
                self.canvas.scan_mark(event.x, event.y)
            return
        # 決定目標頁（連續模式下為游標所在頁）
        if cont:
            e = self._cont_page_at(cx, cy)
            if e is None:
                self._annot_start = None
                return
            self._annot_page = e["idx"]
            self.cur_page = e["idx"]
        else:
            self._annot_page = self.cur_page
        bx, by = self._map_to_target(cx, cy)
        if tool == "select":
            idx = self._hit_test(bx, by)
            self._selected_annot = idx
            self._move_start_base = None
            self._move_orig_coords = None
            self._moving = False
            if idx is not None:
                annots = self.annot_by_page.get(self.cur_page, [])
                self._move_orig_coords = list(annots[idx][1])  # 原始座標複本
                self._move_start_base = (bx, by)
                self.canvas.configure(cursor="fleur")
            self._redraw_after_annot()
            return
        self._annot_start     = (bx, by)
        self._annot_start_cvs = (cx, cy)
        if tool == "pen":
            self._pen_points = [(bx, by)]
        elif tool == "text":
            txt = simpledialog.askstring("文字標註", "輸入文字：")
            if txt:
                self._annot_target_list().append(
                    ("text", [(bx, by)], self.annot_color,
                     self.annot_width / max(self.zoom, 1e-6), txt,
                     16 / max(self.zoom, 1e-6)))
                self._undo_stack.append(
                    ("add", self.cur_page, self.annotations[-1]))
                self._redraw_after_annot()
            self._annot_start = None

    def _on_drag(self, event):
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        tool = self.annot_tool.get()
        cont = self.view_mode.get() == "continuous"
        if tool == "none":
            if self._drag_start:
                if cont:
                    self.canvas.scan_dragto(event.x, event.y, gain=1)
                    self._cont_refresh_visible()
                    self._cont_update_curpage()
                else:
                    self.img_offset[0] += cx - self._drag_start[0]
                    self.img_offset[1] += cy - self._drag_start[1]
                    self._drag_start = (cx, cy)
                    self._render()
            return
        if tool == "select":
            if self._selected_annot is None or self._move_start_base is None:
                return
            bx, by = self._map_to_target(cx, cy)   # 連續模式對應正確頁
            dx = bx - self._move_start_base[0]
            dy = by - self._move_start_base[1]
            if abs(dx) > 1e-6 or abs(dy) > 1e-6:
                self._moving = True
            annots = self.annot_by_page.get(self.cur_page, [])
            i = self._selected_annot
            if 0 <= i < len(annots):
                # 每次都從原始座標重算，避免累積誤差
                new_coords = [(x + dx, y + dy) for (x, y) in self._move_orig_coords]
                a = annots[i]
                annots[i] = (a[0], new_coords, *a[2:])
                self._redraw_after_annot()
            return
        if self._annot_start is None:
            return
        if tool == "pen":
            self._pen_points.append(self._map_to_target(cx, cy))
            self._redraw_after_annot()
        elif tool in ("line", "rect", "oval", "highlight"):
            # 橡皮筋預覽直接用畫布座標
            if self._annot_temp_id:
                self.canvas.delete(self._annot_temp_id)
            c1x, c1y = self._annot_start_cvs
            kw = dict(outline=self.annot_color, width=self.annot_width, dash=(4, 2))
            if tool in ("rect", "highlight"):
                self._annot_temp_id = self.canvas.create_rectangle(
                    c1x, c1y, cx, cy, **kw)
            elif tool == "oval":
                self._annot_temp_id = self.canvas.create_oval(
                    c1x, c1y, cx, cy, **kw)
            elif tool == "line":
                self._annot_temp_id = self.canvas.create_line(
                    c1x, c1y, cx, cy, fill=self.annot_color,
                    width=self.annot_width, dash=(4, 2))

    def _on_release(self, event):
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        tool = self.annot_tool.get()
        self.canvas.configure(cursor="crosshair")
        self._drag_start = None
        if tool == "select":
            if (self._moving and self._selected_annot is not None
                    and self._move_orig_coords is not None):
                self._undo_stack.append(
                    ("move", self.cur_page, self._selected_annot,
                     self._move_orig_coords))
                self._update_status("已移動標註（Ctrl+Z 可復原）")
            self._move_start_base = None
            self._move_orig_coords = None
            self._moving = False
            return
        if tool == "none" or self._annot_start is None:
            self._annot_start = None
            return
        if self._annot_temp_id:
            self.canvas.delete(self._annot_temp_id)
            self._annot_temp_id = None
        wbase = self.annot_width / max(self.zoom, 1e-6)
        if tool == "pen":
            if len(self._pen_points) > 1:
                self._annot_target_list().append(
                    ("pen", list(self._pen_points), self.annot_color, wbase))
                self._undo_stack.append(
                    ("add", self.cur_page, self.annotations[-1]))
        elif tool in ("line", "rect", "oval", "highlight"):
            bx, by = self._map_to_target(cx, cy)
            self._annot_target_list().append(
                (tool, [self._annot_start, (bx, by)], self.annot_color, wbase))
            self._undo_stack.append(
                ("add", self.cur_page, self.annotations[-1]))
        self._redraw_after_annot()
        self._annot_start = None
        self._annot_page  = None

    def _on_scroll(self, event):
        if self.view_mode.get() == "continuous":
            _, ch = self._cont_content
            if ch > 1:
                step = -event.delta / 120 * 90 / ch   # 每格約 90px
                top  = self.canvas.canvasy(0) / ch
                self.canvas.yview_moveto(min(max(top + step, 0), 1))
                self._cont_refresh_visible()
                self._cont_update_curpage()
        elif len(self.pages) > 1:
            self._next_page() if event.delta < 0 else self._prev_page()

    def _on_ctrl_scroll(self, event):
        self._zoom_in() if event.delta > 0 else self._zoom_out()

    # ── 縮放 / 旋轉 / 頁面 ───────────────────────────────────────────────────
    def _post_zoom(self):
        """套用縮放後重繪；連續模式維持目前頁在視窗內。"""
        self.img_offset = [0, 0]
        self._render()
        if self.view_mode.get() == "continuous":
            self._cont_scroll_to_page(self.cur_page)

    def _zoom_in(self):
        self.zoom = min(self.zoom * 1.25, 20.0)
        self._post_zoom()

    def _zoom_out(self):
        self.zoom = max(self.zoom / 1.25, 0.05)
        self._post_zoom()

    def _zoom_fit(self):
        self.update_idletasks()
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw <= 1 or ch <= 1:        # 視窗尚未配置好，稍後再試
            self.after(60, self._zoom_fit)
            return
        W0, H0 = self._base_size(self.cur_page)
        if self.rotation in (90, 270):
            W0, H0 = H0, W0
        if self.view_mode.get() == "continuous":
            # 連續模式：符合頁寬
            self.zoom = max(0.05, (cw - 2 * self._cont_margin) / max(W0, 1))
        else:
            self.zoom = max(0.05, min((cw - 40) / max(W0, 1),
                                      (ch - 40) / max(H0, 1)))
        self._post_zoom()

    def _zoom_actual(self):
        self.zoom = 1.0
        self._post_zoom()

    def _rotate(self, deg):
        self.rotation = (self.rotation + deg) % 360
        self._render()
        if self.view_mode.get() == "continuous":
            self._cont_scroll_to_page(self.cur_page)

    def _go_page(self, idx):
        if 0 <= idx < len(self.pages):
            self.cur_page = idx
            self.img_offset = [0, 0]
            self._selected_annot = None
            if self.view_mode.get() == "continuous":
                self._cont_scroll_to_page(idx)
                total = len(self.pages)
                self.page_label.configure(text=f"{idx + 1} / {total}")
                self._highlight_thumb(idx)
            else:
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
        old = self.annot_by_page.pop(self.cur_page, None)
        if old:
            self._undo_stack.append(("clear", self.cur_page, old))
        self._selected_annot = None
        self._render()

    def _undo(self):
        """Ctrl+Z：回退最近一次標註動作（新增 / 刪除 / 清除整頁）。"""
        if not self._undo_stack:
            self._update_status("沒有可復原的動作")
            return
        action = self._undo_stack.pop()
        kind, page = action[0], action[1]
        if kind == "add":                      # 復原新增 → 依物件移除
            obj = action[2]
            lst = self.annot_by_page.get(page)
            if lst:
                for i in range(len(lst) - 1, -1, -1):
                    if lst[i] is obj:
                        del lst[i]
                        break
                if not lst:
                    self.annot_by_page.pop(page, None)
        elif kind == "del":                    # 復原刪除 → 插回原位
            _, page, idx, obj = action
            lst = self.annot_by_page.setdefault(page, [])
            lst.insert(min(idx, len(lst)), obj)
        elif kind == "move":                   # 復原搬移 → 還原座標
            _, page, idx, old_coords = action
            lst = self.annot_by_page.get(page)
            if lst and 0 <= idx < len(lst):
                a = lst[idx]
                lst[idx] = (a[0], old_coords, *a[2:])
        elif kind == "clear":                  # 復原整頁清除
            self.annot_by_page[page] = action[2]
        self._selected_annot = None
        # 切到受影響的頁面再重畫，讓使用者看到復原結果
        if page != self.cur_page and 0 <= page < len(self.pages):
            self._go_page(page)
        else:
            self._render()
        self._update_status("已復原")

    # ── 標註選取 / 刪除 ────────────────────────────────────────────────────────
    def _on_tool_change(self, *_):
        """切換工具時取消選取，避免殘留選取框。"""
        if self._selected_annot is not None:
            self._selected_annot = None
            self._render()

    @staticmethod
    def _point_seg_dist(px, py, ax, ay, bx, by):
        """點 (px,py) 到線段 AB 的距離。"""
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        cx, cy = ax + t * dx, ay + t * dy
        return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5

    def _annot_bbox(self, annot):
        """回傳標註在基底座標的外接框 (x0, y0, x1, y1)。"""
        t, coords = annot[0], annot[1]
        xs = [p[0] for p in coords]; ys = [p[1] for p in coords]
        if t == "text":
            txt   = annot[4] if len(annot) > 4 else ""
            tbase = annot[5] if len(annot) > 5 else 16
            x, y = coords[0]
            return (x, y, x + max(1, len(txt)) * tbase * 0.6, y + tbase)
        return (min(xs), min(ys), max(xs), max(ys))

    def _hit_test(self, bx, by):
        """傳回基底座標 (bx,by) 命中的標註索引（由上層往下找），沒有則 None。"""
        annots = self.annot_by_page.get(self.cur_page, [])
        tol = max(4.0, 6.0 / max(self.zoom, 1e-6))   # 容許誤差（基底單位）
        for i in range(len(annots) - 1, -1, -1):     # 後畫的在上層，優先命中
            annot = annots[i]
            t, coords = annot[0], annot[1]
            if t in ("pen", "line"):
                hit = any(
                    self._point_seg_dist(bx, by, coords[j][0], coords[j][1],
                                         coords[j + 1][0], coords[j + 1][1]) <= tol
                    for j in range(len(coords) - 1))
            else:  # rect / oval / highlight / text → 外接框內即命中
                x0, y0, x1, y1 = self._annot_bbox(annot)
                hit = (x0 - tol <= bx <= x1 + tol) and (y0 - tol <= by <= y1 + tol)
            if hit:
                return i
        return None

    def _delete_selected(self):
        if self.annot_tool.get() != "select" or self._selected_annot is None:
            return
        lst = self.annot_by_page.get(self.cur_page)
        idx = self._selected_annot
        if not lst or not (0 <= idx < len(lst)):
            self._selected_annot = None
            return
        obj = lst.pop(idx)
        self._undo_stack.append(("del", self.cur_page, idx, obj))
        if not lst:
            self.annot_by_page.pop(self.cur_page, None)
        self._selected_annot = None
        self._render()
        self._update_status("已刪除標註（Ctrl+Z 可復原）")

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

    def _render_annot_overlay(self, page_idx: int) -> Image.Image | None:
        """產生一張基底大小、未旋轉的透明 PNG，只含該頁標註。"""
        annots = self.annot_by_page.get(page_idx, [])
        if not annots:
            return None
        W0, H0 = self._base_size(page_idx)
        overlay = Image.new("RGBA", (W0, H0), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        # 基底空間 → 基底空間（identity），標註以原始比例繪製
        self._draw_annotations(draw, overlay, annots,
                               to_disp=lambda x, y: (x, y), scale=1.0)
        return overlay

    @staticmethod
    def _overlay_to_page(overlay: Image.Image, w: float, h: float):
        """把一張 RGBA 標註圖轉成 w×h 點的單頁 PDF（保留透明度）。"""
        from pypdf import PdfReader
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.lib.utils import ImageReader
        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=(w, h))
        # mask='auto'：reportlab 依 alpha 自動建立 SMask，底下內容（文字）保持可見
        c.drawImage(ImageReader(overlay), 0, 0, width=w, height=h, mask="auto")
        c.showPage()
        c.save()
        buf.seek(0)
        return PdfReader(buf).pages[0]

    @staticmethod
    def _image_to_page(img: Image.Image, overlay, w: float, h: float):
        """把整頁圖片（+選用標註層）轉成 w×h 點的單頁 PDF。"""
        from pypdf import PdfReader
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.lib.utils import ImageReader
        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=(w, h))
        c.drawImage(ImageReader(img), 0, 0, width=w, height=h)
        if overlay is not None:
            c.drawImage(ImageReader(overlay), 0, 0, width=w, height=h, mask="auto")
        c.showPage()
        c.save()
        buf.seek(0)
        return PdfReader(buf).pages[0]

    def _export_all_as_pdf(self, out_path: Path):
        """匯出所有頁面；PDF 頁直接複製原頁（保留可選取文字），標註以透明圖層疊加。"""
        try:
            from pypdf import PdfReader, PdfWriter
        except ImportError:
            messagebox.showerror(
                "缺少套件", "匯出 PDF 需要 pypdf 與 reportlab：\n"
                "pip install pypdf reportlab")
            return
        try:
            writer = PdfWriter()
            reader_cache: dict[str, "PdfReader"] = {}
            for i, entry in enumerate(self.pages):
                overlay = self._render_annot_overlay(i)
                if entry.kind == "pdf":
                    src = str(entry.source_path)
                    reader = reader_cache.get(src)
                    if reader is None:
                        reader = PdfReader(src)
                        reader_cache[src] = reader
                    page = reader.pages[entry.page_idx]   # 原頁，文字/向量原封不動
                    if overlay is not None:
                        box = page.mediabox
                        ov = self._overlay_to_page(
                            overlay, float(box.width), float(box.height))
                        page.merge_page(ov)               # 標註疊在原頁上方
                    writer.add_page(page)
                else:
                    img = entry.pil
                    if img.mode not in ("RGB", "RGBA"):
                        img = img.convert("RGB")
                    # 沿用舊行為：圖片像素數 = PDF 點數（1px = 1pt）
                    page = self._image_to_page(img, overlay, img.width, img.height)
                    writer.add_page(page)
            with open(out_path, "wb") as f:
                writer.write(f)
            self._update_status(f"已匯出 PDF：{out_path}（{len(self.pages)} 頁）")
        except Exception as e:
            messagebox.showerror("匯出失敗", str(e))

    def _save_cur_page_as_image(self, path: Path):
        """匯出目前頁為圖片（含旋轉與標註，zoom=1.0 解析度）。"""
        try:
            img = self._render_page(self.cur_page, zoom=1.0)
            if img is None:
                return
            if img.mode != "RGB":
                img = img.convert("RGB")
            W0, H0 = self._base_size(self.cur_page)
            rot = self.rotation

            def to_disp(x, y):
                if   rot == 90:  bx, by = H0 - y, x
                elif rot == 180: bx, by = W0 - x, H0 - y
                elif rot == 270: bx, by = y, W0 - x
                else:            bx, by = x, y
                return bx, by  # zoom=1.0

            draw = ImageDraw.Draw(img)
            self._draw_annotations(
                draw, img, self.annot_by_page.get(self.cur_page, []),
                to_disp=to_disp, scale=1.0)
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
