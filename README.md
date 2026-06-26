# WinPreview

A free, single-app alternative to macOS **Preview** for Windows — an integrated
image viewer and PDF annotator in one lightweight window.

Windows 版「預覽程式」：把圖片瀏覽與 PDF 標註整合在單一視窗，內建、免費、免安裝。

![status](https://img.shields.io/badge/release-v0.1-blue)

## ✨ 功能 Features

- **圖文 + PDF 統一檢視**：JPG / PNG / GIF / BMP / TIFF / WebP / ICO 與多頁 PDF
- **拖放累加**：把多個檔案拖進視窗會「累加」成一份多頁文件（而非覆蓋）
- **頁面側欄**：縮圖瀏覽、**拖曳調整順序**、`Del` 鍵刪除頁面
- **標註工具**：畫筆、直線、矩形、橢圓、文字、螢光筆，多種顏色
  - 標註以「頁面原始座標」儲存，**縮放、旋轉、翻頁皆不跑位**
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

## ⌨️ 快捷鍵 Shortcuts

| 按鍵 | 功能 |
|------|------|
| `Ctrl+O` | 開啟（可多選） |
| `Ctrl+S` | 儲存 / 匯出 |
| `Ctrl++` / `Ctrl+-` | 放大 / 縮小 |
| `Ctrl+0` / `Ctrl+1` | 符合視窗 / 實際大小 |
| `Ctrl+L` / `Ctrl+R` | 左轉 / 右轉 90° |
| `PageUp` / `PageDown` | 上一頁 / 下一頁 |
| `Del`（縮圖區） | 刪除目前頁 |

## 🛠 技術 Built with

Python · tkinter · [PyMuPDF](https://pymupdf.readthedocs.io/) · [Pillow](https://python-pillow.org/) · tkinterdnd2

## 授權 License

[MIT](LICENSE)
