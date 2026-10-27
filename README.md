# VideoFixer Pro | 影片卡頓檢測與極速修復大師

[![GitHub Release](https://img.shields.io/github/v/release/marker1111014/video_GOP_fix?style=flat-square&color=0e639c)](https://github.com/marker1111014/video_GOP_fix/releases)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11%20(x64)-181818?style=flat-square)](https://github.com/marker1111014/video_GOP_fix)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![GUI](https://img.shields.io/badge/GUI-PyQt6-41CD52?style=flat-square&logo=qt&logoColor=white)](https://riverbankcomputing.com/software/pyqt/)
[![Hardware](https://img.shields.io/badge/Hardware-NVENC%20%7C%20QSV%20%7C%20AMF-76B900?style=flat-square&logo=nvidia&logoColor=white)](https://developer.nvidia.com/video-encode-decode-gpu-support-matrix)
[![License](https://img.shields.io/badge/License-MIT-gray?style=flat-square)](LICENSE)

專為解決**影片隨意跳轉、拖曳進度條時出現的「黑屏、轉圈、卡頓延遲」**而打造的高效能影音修復工作站。

提供 **100% 綠色免安裝可攜版**（已內嵌硬體加速版 FFmpeg 核心）與完整 Python 原始碼，支援 **NVIDIA NVENC / Intel QuickSync / AMD AMF** 多廠牌顯卡硬解飆速轉碼，並具備專業剪輯工作站級別的極簡直角暗黑介面。

---

## 📌 為什麼播放影片拖曳進度條會卡頓？

許多從網路下載、串流錄製或行車記錄器導出的影片，在一般播放器（PotPlayer、VLC、Windows Media Player 等）中拖動進度條時會嚴重卡頓，主要根源有三：

1. **關鍵影格間隔過大 (GOP 偏長 / 稀疏)**：  
   部分影片為了極致壓縮體積，將關鍵影格 (Keyframe / IDR) 設為 5 秒甚至 10 秒以上。播放器每次跳轉時，必須從前一個關鍵影格硬算解碼數百張預測畫面 (P/B Frame)，造成顯著黑屏延遲。
2. **TS 傳輸流格式無隨機存取索引**：  
   `.ts` 格式本質為廣播切片流，缺乏類似 MP4 的全域尋軌索引表，播放器每次跳轉必須線性掃描資料封包，極易轉圈卡死。
3. **MP4 Moov 索引在檔案結尾 (未開啟 FastStart)**：  
   播放器必須讀取完整個檔案才能解析出畫面座標，大幅降低跳轉反應速度。

---

## ⚡ 核心解決方案與特色功能

- **🚀 治本重建 1.5 秒高密度關鍵影格 (GOP)**：  
  將關鍵影格精準編排為 1.5 秒一幀，兼顧檔案體積與極速秒拖體驗，徹底消滅跳轉延遲。
- **🎮 多廠牌顯卡硬體加速 + CPU 智慧備援**：  
  自動偵測與調用 **NVIDIA NVENC (`h264_nvenc`)**、**Intel QuickSync (`h264_qsv`)**、**AMD AMF (`h264_amf`)**，在 RTX 2060 等級顯卡上實測可達 **10.8x+ 轉碼飆速**；若遭遇環境異常，系統將自動無縫切換為 CPU 多核心 (`libx264`) 智慧備援。
- **🔍 智慧多點快速抽樣檢測 (< 0.5s/部)**：  
  利用前置快速定位技術，毫秒級萃取多個區間的關鍵影格密度與最大 GOP，瞬間診斷整批影片健康度。
- **⚡ TS 格式秒轉 MP4 (無損轉裝)**：  
  對 TS 影片進行無損重封裝（Copy Stream），不損畫質、不耗費轉碼時間，並自動前移 FastStart 索引至檔案頭部。
- **🛡️ 播放相容性雙重保障**：  
  - **色彩格式標準化**：強制約束為 `yuv420p`，防止 10-bit HDR 或 4:4:4 素材轉碼後在老舊螢幕/播放器上出現綠屏或黑屏。
  - **多音軌完整保護**：採用全音訊動態映射（`-map 0:v:0 -map 0:a?`），防止雙語音軌、評論音軌丟失。
- **🎛️ 專業影音工作站風格 GUI (DaVinci / Premiere 風格)**：  
  全元件採用 **0px 銳利直角**、**霧面深炭黑 `#181818`** 與 **零 Emoji 技術文字標記**，支援多檔案/整個資料夾直接拖曳、雙擊呼叫播放器、右鍵檔案總管定位，以及可折疊進階參數抽屜。

---

## 📥 下載與快速開始 (Quick Start)

### 方式一：下載綠色免安裝可攜版 (一般使用者首選)

無須安裝 Python、FFmpeg 或任何相依套件，整包下載解壓即可使用：

1. 前往 **[Releases 最新發布頁面](https://github.com/marker1111014/video_GOP_fix/releases)** 下載最新版本壓縮檔 `影片卡頓檢測與極速修復大師_可攜版.zip`。
2. 解壓縮後進入資料夾，雙擊 **`影片卡頓檢測與極速修復大師.exe`** 或 **`雙擊啟動_影片修復大師.bat`**。
3. 將影片或資料夾直接拖進視窗，點擊「開始批次處理」即可！

---

### 方式二：從原始碼運行 (開發者與進階用戶)

#### 1. 複製倉庫
```bash
git clone https://github.com/marker1111014/video_GOP_fix.git
cd video_GOP_fix
```

#### 2. 安裝相依套件
建議使用 Conda 或 Python 虛擬環境 (Python 3.10+)：
```bash
pip install -r requirements.txt
```

#### 3. 確保系統具備 FFmpeg
- 若系統 PATH 已包含 `ffmpeg.exe`，程式將自動偵測。
- 或將 `ffmpeg.exe` 放置於 `VideoFixer_Portable_Project/bin/` 目錄下。

#### 4. 啟動 GUI 介面
```bash
python VideoFixer_Portable_Project/app.py
```

---

## 🛠️ 命令列工具 (CLI Tools)

若習慣使用終端機或批次排程，本專案亦提供乾淨強大的命令列腳本：

| 腳本名稱 | 一鍵批次檔 | 功能說明 |
| :--- | :--- | :--- |
| `check_videos.py` | `一鍵檢測影片卡頓問題.bat` | 掃描當前目錄所有影片，輸出 GOP 間隔、Moov 位置並列出卡頓風險清單。 |
| `fix_videos_nvenc.py` | `一鍵顯卡修復_零卡頓.bat` | 使用顯卡硬體加速進行 1.5s GOP 高密度重整，修復結果輸出至 `修復_零卡頓/`。 |
| `remux_lossless.py` | `一鍵無損秒轉.bat` | 針對 `.ts` 格式影片進行秒速無損轉封裝為 MP4 並最佳化 FastStart。 |

使用範例：
```bash
# 批次檢測指定目錄
python check_videos.py "D:\Videos"

# 批次使用硬解修復指定目錄
python fix_videos_nvenc.py "D:\Videos"
```

---

## 🏗️ 專案目錄結構

```text
video_GOP_fix/
├── core/                                   # 核心算法與底層庫
│   ├── media_analyzer.py                  # 多點抽樣 GOP 與二進位 Moov 解析器
│   ├── encoder_engine.py                  # 多廠牌硬體加速指令產生器與自動備援
│   ├── ffmpeg_utils.py                    # 跨環境 FFmpeg 探測與子程序保護
│   └── __init__.py
├── VideoFixer_Portable_Project/            # GUI 專案原始碼與打包配置
│   ├── app.py                             # PyQt6 專業工作站 GUI 主程式
│   ├── VideoFixerPro.spec                 # PyInstaller 綠色打包配置
│   └── core/                              # 模組複本
├── check_videos.py                         # CLI 批次分析工具
├── fix_videos_nvenc.py                     # CLI 批次轉碼修復工具
├── remux_lossless.py                       # CLI 批次 TS 轉裝工具
├── 專業工作站直角UI風格提示詞指南.txt        # 供 AI 介面設計複用的完整提示詞與 QSS 規範
├── requirements.txt                       # Python 相依清單
├── .gitignore                             # Git 排除配置
└── README.md                              # 完整說明文件
```

---

## ⚙️ 進階參數微調說明

在軟體介面中點擊 **「進階編碼設定 ▼」** 可展開抽屜微調以下設定：

- **關鍵影格間隔 (GOP)**：預設 `1.5 秒`。數值越低（如 1.0s）拖曳進度條越極速反應；數值調高（如 2.5s）可在長片中進一步降低檔案體積。
- **編碼畫質品質 (CQ / CRF)**：預設 `22`（視覺無損平衡點）。數值範圍 16~28，越低畫質越高、檔案越大。
- **標準 yuv420p 色彩約束**：預設開啟，確保相容於手機、平板、老舊電視與各類網頁播放器。
- **並行任務加速**：支援「1 部 (順序)」或「2 部並行 (利用 GPU 雙編碼通道)」，大幅縮短大量影片修復總耗時。

---

## 📜 授權協議 (License)

本專案採用 [MIT License](LICENSE) 授權開源，歡迎自由使用、修改與整合。
