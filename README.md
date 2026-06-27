# WinPreview

A free, single-app alternative to macOS **Preview** for Windows — an integrated
image viewer and PDF annotator in one lightweight window.

Windows 版「預覽程式」：把圖片瀏覽與 PDF 標註整合在單一視窗，內建、免費、免安裝。

![status](https://img.shields.io/badge/release-v0.2-blue)

## ✨ 功能 Features

- **圖文 + PDF 統一檢視**：JPG / PNG / GIF / BMP / TIFF / WebP / ICO 與多頁 PDF
- **拖放累加**：把多個檔案拖進視窗會「累加」成一份多頁文件（而非覆蓋）
- **頁面側欄**：縮圖瀏覽、**拖曳調整順序**、`Del` 鍵刪除頁面
- **標註工具**：畫筆、直線、矩形、橢圓、文字、螢光筆，多種顏色
  - 標註以「頁面原始座標」儲存，**縮放、旋轉、翻頁皆不跑位**
- **標註編輯**（`▣` 選取工具）
  - **選取**：點標註即選中，顯示藍色選取框（重疊時選最上層）
  - **刪除**：選中後按 `Del` / `Backspace` 移除單一標註
  - **搬移**：按住選中的標註拖曳即可移動位置
  - **復原**：`Ctrl+Z` 可逐步回退新增 / 刪除 / 搬移 / 整頁清除
- **縮放 / 旋轉**：符合視窗、實際大小、`Ctrl+滾輪` 縮放、左右旋轉
- **匯出**
  - 匯出為 **PDF**：合併所有頁面；PDF 原頁保留**可選取文字**，標註以圖層疊加
  - 匯出目前頁為 **圖片**（PNG / JPEG / BMP）

## 🚀 安裝與執行 Run from source

需要 Python 3.10+。

```bat
pip install -r requirements.txt
python main.py
```

或直接執行 `run.bat`（不開主控台視窗）。

> 拖放功能需要 `tkinterdnd2`；若未安裝，其他功能仍可正常使用。

## 📦 打包成 exe Build executable

```bat
pip install pyinstaller
build.bat
```

產出 `dist\WinPreview.exe`（單一檔、免安裝）。打包腳本已用
`--collect-all tkinterdnd2` 確保拖放在 exe 中正常運作。

> 兩種打包形式：`--onefile`（單一 exe，最簡潔）或 `--onedir`（可攜資料夾，
> 防毒誤報機率較低）。Release 同時提供這兩種下載，見下方說明。

## ⚠️ 防毒誤報 Antivirus false positive

下載單一檔 `WinPreview.exe` 時，Windows Defender 可能回報
**`Trojan:Win32/Wacatac.B!ml`**。這是**誤報**：

- 結尾 `!ml` 代表是 Defender 的**機器學習啟發式**判斷，而非已知病毒特徵碼。
- `Wacatac.B!ml` 是「**未簽章的 PyInstaller 單檔 exe**」最常見的誤報標籤；本程式以
  `--onefile` 打包，因「單檔自我解壓」行為被啟發式誤判。原始碼公開，可用
  [VirusTotal](https://www.virustotal.com/) 自行比對。

**兩種因應方式：**

1. **改下載可攜資料夾版（`--onedir`，zip）**：Release 另提供解壓即用的資料夾版，
   誤報機率低很多。解壓後雙擊裡面的 `WinPreview.exe` 即可，一樣免安裝。
2. **保留單一 exe 並加白名單**（Windows 安全性）：
   - **還原**：Windows 安全性 → 病毒與威脅防護 → 保護歷程記錄 → 找到項目 → 還原 / 允許。
   - **排除**：病毒與威脅防護 → 管理設定 → 排除項目 → 新增檔案 / 資料夾。
   - **PowerShell（系統管理員）**：`Add-MpPreference -ExclusionPath "C:\路徑\WinPreview.exe"`

> 以上白名單只對你自己的電腦有效。根本解法是為執行檔加上**程式碼簽章
> （Authenticode）**，未來版本將評估導入。

## ⌨️ 快捷鍵 Shortcuts

| 按鍵 | 功能 |
|------|------|
| `Ctrl+O` | 開啟（可多選） |
| `Ctrl+S` | 儲存 / 匯出 |
| `Ctrl++` / `Ctrl+-` | 放大 / 縮小 |
| `Ctrl+0` / `Ctrl+1` | 符合視窗 / 實際大小 |
| `Ctrl+L` / `Ctrl+R` | 左轉 / 右轉 90° |
| `Ctrl+Z` | 復原標註動作（新增 / 刪除 / 搬移 / 清除） |
| `PageUp` / `PageDown` | 上一頁 / 下一頁 |
| `Del` / `Backspace`（選取標註時） | 刪除選中的標註 |
| `Del`（縮圖區） | 刪除目前頁 |

## 🛠 技術 Built with

Python · tkinter · [pypdfium2](https://github.com/pypdfium2-team/pypdfium2)（PDF 渲染）· [pypdf](https://pypdf.readthedocs.io/)＋[reportlab](https://www.reportlab.com/)（PDF 匯出）· [Pillow](https://python-pillow.org/) · tkinterdnd2

## 🔧 PDF 引擎替換 PDF engine migration

> 本專案原本使用 **PyMuPDF（`fitz`）** 處理 PDF，後來改為
> **pypdfium2 + pypdf + reportlab** 的組合。以下記錄替換的原因、方法與結果。

### 替換原因 Why

| 問題 | 說明 |
|------|------|
| **執行檔過大** | PyMuPDF 內含完整 MuPDF 原生庫，打包後通常吃掉 20–35MB，是 exe 體積的最大宗。 |
| **授權衝突** | PyMuPDF 為 **AGPL-3.0**（或需付費商業授權），與本專案的 **MIT** 授權並不相容。 |

### 替換方法 How

把 PyMuPDF「渲染 + 編輯」的雙重職責拆給專職且授權友善（Apache / BSD）的套件：

| 職責 | 原本（PyMuPDF） | 改用 | 授權 |
|------|----------------|------|------|
| **畫面渲染**（顯示 / 翻頁 / 縮放 / 旋轉、圖片匯出） | `get_pixmap` | **pypdfium2**（Google PDFium） | Apache / BSD |
| **複製原頁**（匯出時保留可選取文字） | `show_pdf_page` | **pypdf** `add_page`（直接複製頁物件） | BSD |
| **標註疊層**（透明圖層疊在原頁上方） | `insert_image(overlay)` | **reportlab** `drawImage(mask="auto")` ＋ pypdf `merge_page` | BSD |

關鍵在於匯出時，原頁以 pypdf **直接複製**（文字 / 向量原封不動、仍可選取），標註層則由
reportlab 以 **alpha 透明**方式疊上，因此底層文字不會被覆蓋。座標仍沿用「基底空間」
（未旋轉、zoom=1.0 的頁面像素）慣例，標註位置不受影響。

### 替換結果 Result

- 📦 **體積大幅縮小**：PDF 原生庫從約 20–35MB（MuPDF）降到約 5MB（PDFium）。
- ⚖️ **授權乾淨**：全部相依改為 Apache / BSD，與 MIT 一致，可自由散布。
- ✅ **功能不變且更佳**：匯出 PDF 仍保留可選取文字；標註疊層透明度正確。

## 📜 變更紀錄 Changelog

### v0.2

- **PDF 引擎替換**：PyMuPDF（`fitz`）→ **pypdfium2 + pypdf + reportlab**。
  - 解決 v0.1 的**授權不相容**：PyMuPDF 為 AGPL-3.0，與本專案 MIT 衝突；新組合全為 Apache / BSD。
  - 執行檔顯著縮小（PDF 原生庫由約 20–35MB 降至約 5MB）。
- **新增標註編輯**（`▣` 選取工具）：選取、刪除（`Del`）、拖曳搬移。
- **新增復原**：`Ctrl+Z` 可回退新增 / 刪除 / 搬移 / 整頁清除。

> ⚠️ **關於 v0.1**：v0.1 發佈的 `WinPreview.exe` 內含 AGPL 授權的 PyMuPDF，
> 與專案宣告的 MIT 不相容，該執行檔已不再提供；請改用 v0.2。

### v0.1

- 初版：圖片瀏覽 + PDF 標註整合、拖放累加、頁面側欄、匯出 PDF / 圖片。

## 授權 License

[MIT](LICENSE)
