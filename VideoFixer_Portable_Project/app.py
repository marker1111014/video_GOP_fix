import os
import sys
import re
import time
import shutil
import subprocess
import webbrowser
from datetime import datetime
import concurrent.futures

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QRadioButton, QButtonGroup, QFileDialog, QGroupBox,
    QMessageBox, QAbstractItemView, QMenu, QFrame, QSlider, QComboBox,
    QCheckBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QAction, QCursor

# Ensure UTF-8 on Windows
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Import core modules
try:
    from core.ffmpeg_utils import find_ffmpeg, check_nvenc, get_subprocess_flags
    from core.media_analyzer import analyze_video, check_moov_atom, probe_media_info
    from core.encoder_engine import detect_hardware_encoders, build_transcode_command, build_remux_command
except ImportError:
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cur_dir = os.path.dirname(os.path.abspath(__file__))
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    if cur_dir not in sys.path:
        sys.path.insert(0, cur_dir)
    from core.ffmpeg_utils import find_ffmpeg, check_nvenc, get_subprocess_flags
    from core.media_analyzer import analyze_video, check_moov_atom, probe_media_info
    from core.encoder_engine import detect_hardware_encoders, build_transcode_command, build_remux_command

get_ffmpeg_path = find_ffmpeg
analyze_video_file = analyze_video


class AnalysisWorker(QThread):
    sig_progress = pyqtSignal(int, int, str)
    sig_row_done = pyqtSignal(int, dict)
    sig_all_done = pyqtSignal()

    def __init__(self, ffmpeg_path, tasks):
        super().__init__()
        self.ffmpeg_path = ffmpeg_path
        self.tasks = tasks  # list of (row_idx, file_path)
        self.is_cancelled = False

    def run(self):
        total = len(self.tasks)
        for i, (row_idx, file_path) in enumerate(self.tasks, 1):
            if self.is_cancelled:
                break
            fname = os.path.basename(file_path)
            self.sig_progress.emit(i, total, fname)
            info = analyze_video(self.ffmpeg_path, file_path)
            self.sig_row_done.emit(row_idx, info)
        self.sig_all_done.emit()

    def cancel(self):
        self.is_cancelled = True


class RepairWorker(QThread):
    sig_overall = pyqtSignal(int, int)  # completed, total
    sig_item_progress = pyqtSignal(int, float, str, str)  # row, pct, speed, eta
    sig_item_done = pyqtSignal(int, bool, float, str, str)  # row, success, size_mb, outpath, msg
    sig_all_done = pyqtSignal(int, int, str)  # success_cnt, fail_cnt, last_out_dir

    def __init__(self, ffmpeg_path, tasks, mode, out_mode, custom_out_dir,
                 encoder_choice='auto', gop_sec=1.5, cq=22, audio_mode='copy',
                 concurrency=1):
        super().__init__()
        self.ffmpeg_path = ffmpeg_path
        self.tasks = tasks  # list of (row_idx, file_path, fps_val)
        self.mode = mode  # 'repair', 'remux_ts'
        self.out_mode = out_mode  # 'default_subfolder', 'custom'
        self.custom_out_dir = custom_out_dir
        self.encoder_choice = encoder_choice
        self.gop_sec = gop_sec
        self.cq = cq
        self.audio_mode = audio_mode
        self.concurrency = concurrency
        self.is_cancelled = False
        self.active_procs = {}

    def cancel(self):
        self.is_cancelled = True
        for proc in list(self.active_procs.values()):
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass

    def run_ffmpeg_encode(self, cmd, total_dur, row_idx):
        flags = get_subprocess_flags()
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='ignore',
            creationflags=flags
        )
        self.active_procs[row_idx] = proc

        time_pattern = re.compile(r'time=([0-9]+):([0-9]+):([0-9.]+)')
        speed_pattern = re.compile(r'speed=\s*([0-9.]+)x')

        start_t = time.time()
        last_emit = 0
        err_lines = []

        while True:
            if self.is_cancelled:
                proc.terminate()
                break
            line = proc.stderr.readline()
            if not line and proc.poll() is not None:
                break

            if line:
                err_lines.append(line)
                if len(err_lines) > 20:
                    err_lines.pop(0)

            now = time.time()
            if now - last_emit > 0.4:
                tm = time_pattern.search(line)
                sm = speed_pattern.search(line)
                if tm and total_dur > 0:
                    cur_sec = float(tm.group(1)) * 3600 + float(tm.group(2)) * 60 + float(tm.group(3))
                    pct = min(99.9, (cur_sec / total_dur) * 100)
                    speed_str = sm.group(1) if sm else '?'
                    elapsed = now - start_t
                    eta_sec = (total_dur - cur_sec) / (cur_sec / elapsed) if cur_sec > 0 and cur_sec < total_dur else 0
                    eta_str = f"{int(eta_sec // 60)}分{int(eta_sec % 60):02d}秒"
                    self.sig_item_progress.emit(row_idx, pct, speed_str, eta_str)
                    last_emit = now

        proc.wait()
        self.active_procs.pop(row_idx, None)
        return proc.returncode, "".join(err_lines)

    def process_single_task(self, task):
        if self.is_cancelled:
            return False, ""

        row_idx, in_path, fps_val = task
        fname = os.path.basename(in_path)
        base_no_ext, ext = os.path.splitext(fname)

        if self.out_mode == 'custom' and self.custom_out_dir:
            target_dir = self.custom_out_dir
        else:
            target_dir = os.path.join(os.path.dirname(in_path), '修復_零卡頓')

        os.makedirs(target_dir, exist_ok=True)
        out_path = os.path.join(target_dir, base_no_ext + '.mp4')
        flags = get_subprocess_flags()

        # Mode 1: TS to MP4 Remux
        if self.mode == 'remux_ts':
            if ext.lower() != '.ts':
                self.sig_item_done.emit(row_idx, False, 0.0, "", "[跳過] 非TS格式")
                return False, target_dir

            cmd = build_remux_command(self.ffmpeg_path, in_path, out_path)
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                        encoding='utf-8', errors='ignore', creationflags=flags)
                self.active_procs[row_idx] = proc
                _, _ = proc.communicate()
                self.active_procs.pop(row_idx, None)

                if proc.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    size_mb = os.path.getsize(out_path) / (1024 * 1024)
                    self.sig_item_progress.emit(row_idx, 100.0, "-", "完成")
                    self.sig_item_done.emit(row_idx, True, size_mb, out_path, "[完成] 無損秒轉")
                    return True, target_dir
                else:
                    if os.path.exists(out_path) and os.path.getsize(out_path) == 0:
                        os.remove(out_path)
                    self.sig_item_done.emit(row_idx, False, 0.0, "", "[失敗] 轉裝錯誤")
                    return False, target_dir
            except Exception as e:
                self.sig_item_done.emit(row_idx, False, 0.0, "", f"[異常] {str(e)[:25]}")
                return False, target_dir

        # Mode 2: Keyframe Dense Transcoding (Hardware GPU or CPU)
        meta = probe_media_info(self.ffmpeg_path, in_path)
        total_dur = meta['duration_sec']
        detected_fps = meta['fps']

        try:
            fps = float(fps_val) if fps_val and fps_val != '未知' else detected_fps
        except ValueError:
            fps = detected_fps

        gop_size = max(15, int(fps * self.gop_sec))
        keyint_min = max(10, int(fps * (self.gop_sec * 0.5)))

        try:
            cmd, active_enc = build_transcode_command(
                self.ffmpeg_path, in_path, out_path,
                encoder_name=self.encoder_choice,
                gop_size=gop_size,
                keyint_min=keyint_min,
                cq=self.cq,
                audio_mode=self.audio_mode
            )
            ret, _ = self.run_ffmpeg_encode(cmd, total_dur, row_idx)

            # Auto fallback to CPU if GPU failed
            if ret != 0 and active_enc != 'libx264' and not self.is_cancelled:
                if os.path.exists(out_path):
                    try:
                        os.remove(out_path)
                    except Exception:
                        pass
                self.sig_item_progress.emit(row_idx, 0.0, "CPU", f"{active_enc} 異常，切換CPU...")
                cmd_cpu, _ = build_transcode_command(
                    self.ffmpeg_path, in_path, out_path,
                    encoder_name='libx264',
                    gop_size=gop_size,
                    keyint_min=keyint_min,
                    cq=self.cq,
                    audio_mode=self.audio_mode
                )
                ret, _ = self.run_ffmpeg_encode(cmd_cpu, total_dur, row_idx)

            if not self.is_cancelled and ret == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                size_mb = os.path.getsize(out_path) / (1024 * 1024)
                self.sig_item_progress.emit(row_idx, 100.0, "-", "完成")
                self.sig_item_done.emit(row_idx, True, size_mb, out_path, "[完成] 零卡頓修復")
                return True, target_dir
            else:
                if os.path.exists(out_path) and os.path.getsize(out_path) == 0:
                    try:
                        os.remove(out_path)
                    except Exception:
                        pass
                if not self.is_cancelled:
                    self.sig_item_done.emit(row_idx, False, 0.0, "", "[失敗] 轉碼錯誤")
                return False, target_dir
        except Exception as e:
            if os.path.exists(out_path) and os.path.getsize(out_path) == 0:
                try:
                    os.remove(out_path)
                except Exception:
                    pass
            self.sig_item_done.emit(row_idx, False, 0.0, "", f"[異常] {str(e)[:25]}")
            return False, target_dir

    def run(self):
        total = len(self.tasks)
        success_cnt = 0
        fail_cnt = 0
        last_out_dir = ""
        completed = 0

        # Run with ThreadPoolExecutor (1 or 2 workers)
        workers_count = min(self.concurrency, max(1, total))

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers_count) as executor:
            future_to_task = {executor.submit(self.process_single_task, task): task for task in self.tasks}
            for future in concurrent.futures.as_completed(future_to_task):
                if self.is_cancelled:
                    break
                try:
                    success, target_dir = future.result()
                    if target_dir:
                        last_out_dir = target_dir
                    if success:
                        success_cnt += 1
                    else:
                        fail_cnt += 1
                except Exception:
                    fail_cnt += 1

                completed += 1
                self.sig_overall.emit(completed, total)

        self.sig_all_done.emit(success_cnt, fail_cnt, last_out_dir)


class VideoFixerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VIDEOFIXER PRO | 影片卡頓檢測與極速修復工作站")
        self.resize(1220, 780)
        self.setMinimumSize(980, 620)
        self.setAcceptDrops(True)

        self.ffmpeg_path = get_ffmpeg_path()
        self.hw_info = detect_hardware_encoders(self.ffmpeg_path) if self.ffmpeg_path else {
            'has_hardware_accel': False,
            'active_encoder': 'libx264',
            'encoders': {}
        }
        self.has_hardware = self.hw_info.get('has_hardware_accel', False)

        self.analysis_worker = None
        self.repair_worker = None
        self.cached_results = {}
        self.last_repaired_dir = ""
        self.drawer_visible = False

        self.init_ui()
        self.apply_dark_theme()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(8)

        # ----------------------------------------------------
        # 1. Header Banner
        # ----------------------------------------------------
        header_frame = QFrame()
        header_frame.setObjectName("HeaderFrame")
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(14, 10, 14, 10)

        title_vbox = QVBoxLayout()
        title_lbl = QLabel("VIDEOFIXER PRO | 影片卡頓檢測與極速修復工作站")
        title_lbl.setStyleSheet("font-size: 15px; font-weight: bold; color: #e2e2e2; letter-spacing: 0.5px;")
        sub_lbl = QLabel("智慧 GOP 取樣檢測 ｜ 治本重建 1.5s 關鍵影格 ｜ 消除拖曳黑屏延遲 ｜ 多晶片硬體加速")
        sub_lbl.setStyleSheet("font-size: 11px; color: #888888;")
        title_vbox.addWidget(title_lbl)
        title_vbox.addWidget(sub_lbl)
        header_layout.addLayout(title_vbox)

        header_layout.addStretch()

        hw_box = QVBoxLayout()
        hw_box.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if self.has_hardware:
            active_key = self.hw_info['active_encoder']
            active_name = self.hw_info['encoders'][active_key]['name']
            badge_lbl = QLabel(f"[硬體加速] {active_name} 已就緒")
            badge_lbl.setStyleSheet(
                "background-color: #1a261c; color: #4ade80; font-weight: bold; padding: 4px 10px; border-radius: 0px; border: 1px solid #2a402d;"
            )
        else:
            badge_lbl = QLabel("[軟體編碼] CPU 多核心模式")
            badge_lbl.setStyleSheet(
                "background-color: #222222; color: #888888; font-weight: bold; padding: 4px 10px; border-radius: 0px; border: 1px solid #333333;"
            )

        ffmpeg_lbl = QLabel(f"核心: {'內嵌 FFmpeg 7.1 (可攜版)' if self.ffmpeg_path else '未檢測到 FFmpeg'}")
        ffmpeg_lbl.setStyleSheet("font-size: 11px; color: #666666; margin-top: 3px;")
        hw_box.addWidget(badge_lbl)
        hw_box.addWidget(ffmpeg_lbl)
        header_layout.addLayout(hw_box)

        main_layout.addWidget(header_frame)

        # ----------------------------------------------------
        # 2. Top Dashboard 3-Card Grid
        # ----------------------------------------------------
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(8)

        # --- Card 1: 任務概況與過濾 ---
        card_stats = QFrame()
        card_stats.setObjectName("CardOverview")
        c1_vbox = QVBoxLayout(card_stats)
        c1_vbox.setContentsMargins(12, 10, 12, 10)
        c1_title = QLabel("任務狀態與過濾")
        c1_title.setStyleSheet("font-weight: bold; color: #d0d0d0; font-size: 12.5px;")
        c1_vbox.addWidget(c1_title)

        self.lbl_stat_line = QLabel("總計: 0 部 ｜ 已選: 0 部 ｜ 卡頓風險: 0 部")
        self.lbl_stat_line.setStyleSheet("color: #999999; font-size: 12px;")
        c1_vbox.addWidget(self.lbl_stat_line)

        c1_btn_layout = QHBoxLayout()
        c1_btn_layout.setSpacing(6)

        btn_sel_all = QPushButton("全選")
        btn_sel_all.clicked.connect(lambda: self.set_all_checked(True))
        c1_btn_layout.addWidget(btn_sel_all)

        btn_sel_none = QPushButton("全不選")
        btn_sel_none.clicked.connect(lambda: self.set_all_checked(False))
        c1_btn_layout.addWidget(btn_sel_none)

        btn_filter_lag = QPushButton("篩選卡頓影片")
        btn_filter_lag.setObjectName("BtnFilterLag")
        btn_filter_lag.clicked.connect(self.select_only_lagging)
        c1_btn_layout.addWidget(btn_filter_lag)

        c1_vbox.addLayout(c1_btn_layout)
        cards_layout.addWidget(card_stats, stretch=3)

        # --- Card 2: 處理模式預設 ---
        card_modes = QFrame()
        card_modes.setObjectName("CardPresets")
        c2_vbox = QVBoxLayout(card_modes)
        c2_vbox.setContentsMargins(12, 10, 12, 10)
        c2_title = QLabel("處理模式預設")
        c2_title.setStyleSheet("font-weight: bold; color: #d0d0d0; font-size: 12.5px;")
        c2_vbox.addWidget(c2_title)

        self.rb_nvenc = QRadioButton("零卡頓修復 (重建 1.5s 關鍵影格，消除拖曳延遲首選)")
        self.rb_nvenc.setChecked(True)
        self.rb_remux = QRadioButton("TS 封裝轉換 (無損秒轉 MP4 + 索引前置)")
        self.rb_detect_only = QRadioButton("僅分析檢測 (掃描關鍵影格與卡頓風險，不修改原檔)")

        self.mode_group = QButtonGroup()
        self.mode_group.addButton(self.rb_nvenc)
        self.mode_group.addButton(self.rb_remux)
        self.mode_group.addButton(self.rb_detect_only)

        c2_vbox.addWidget(self.rb_nvenc)
        c2_vbox.addWidget(self.rb_remux)
        c2_vbox.addWidget(self.rb_detect_only)
        cards_layout.addWidget(card_modes, stretch=4)

        # --- Card 3: 編碼硬體與佇列 ---
        card_hw = QFrame()
        card_hw.setObjectName("CardEngine")
        c3_vbox = QVBoxLayout(card_hw)
        c3_vbox.setContentsMargins(12, 10, 12, 10)
        c3_title = QLabel("編碼硬體與佇列")
        c3_title.setStyleSheet("font-weight: bold; color: #d0d0d0; font-size: 12.5px;")
        c3_vbox.addWidget(c3_title)

        c3_grid = QHBoxLayout()
        lbl_enc = QLabel("硬體晶片：")
        lbl_enc.setStyleSheet("color: #888888; font-size: 12px;")
        self.combo_encoder = QComboBox()
        self.combo_encoder.addItem("自動偵測 (推薦)", "auto")
        if self.hw_info.get('encoders', {}).get('h264_nvenc', {}).get('available'):
            self.combo_encoder.addItem("NVIDIA NVENC (RTX/GTX)", "h264_nvenc")
        if self.hw_info.get('encoders', {}).get('h264_qsv', {}).get('available'):
            self.combo_encoder.addItem("Intel QuickSync (QSV)", "h264_qsv")
        if self.hw_info.get('encoders', {}).get('h264_amf', {}).get('available'):
            self.combo_encoder.addItem("AMD AMF (Radeon)", "h264_amf")
        self.combo_encoder.addItem("CPU 多核心 (x264)", "libx264")
        c3_grid.addWidget(lbl_enc)
        c3_grid.addWidget(self.combo_encoder)
        c3_vbox.addLayout(c3_grid)

        c3_queue_layout = QHBoxLayout()
        lbl_queue = QLabel("並行任務：")
        lbl_queue.setStyleSheet("color: #888888; font-size: 12px;")
        self.combo_concurrency = QComboBox()
        self.combo_concurrency.addItem("1 部 (順序處理)", 1)
        self.combo_concurrency.addItem("2 部並行 (多工加速)", 2)
        c3_queue_layout.addWidget(lbl_queue)
        c3_queue_layout.addWidget(self.combo_concurrency)
        c3_vbox.addLayout(c3_queue_layout)

        cards_layout.addWidget(card_hw, stretch=3)
        main_layout.addLayout(cards_layout)

        # ----------------------------------------------------
        # 3. Toolbar & Video Table
        # ----------------------------------------------------
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setSpacing(6)

        btn_add_files = QPushButton("加入檔案...")
        btn_add_files.setObjectName("BtnPrimaryAction")
        btn_add_files.clicked.connect(self.on_add_files)
        toolbar_layout.addWidget(btn_add_files)

        btn_add_folder = QPushButton("加入資料夾...")
        btn_add_folder.clicked.connect(self.on_add_folder)
        toolbar_layout.addWidget(btn_add_folder)

        btn_remove = QPushButton("移除選取")
        btn_remove.clicked.connect(self.on_remove_selected)
        toolbar_layout.addWidget(btn_remove)

        btn_clear = QPushButton("清空清單")
        btn_clear.clicked.connect(self.on_clear_all)
        toolbar_layout.addWidget(btn_clear)

        toolbar_layout.addSpacing(10)

        self.btn_detect = QPushButton("檢測勾選項目")
        self.btn_detect.clicked.connect(self.on_start_detect)
        toolbar_layout.addWidget(self.btn_detect)

        self.btn_report = QPushButton("產生診斷報告 (HTML)")
        self.btn_report.clicked.connect(self.on_view_report)
        toolbar_layout.addWidget(self.btn_report)

        toolbar_layout.addStretch()

        # Drawer Toggle Button
        self.btn_toggle_drawer = QPushButton("進階編碼設定 ▼")
        self.btn_toggle_drawer.setObjectName("BtnToggleDrawer")
        self.btn_toggle_drawer.clicked.connect(self.toggle_drawer)
        toolbar_layout.addWidget(self.btn_toggle_drawer)

        main_layout.addLayout(toolbar_layout)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels([
            "選取", "檔案名稱 (支援拖曳檔案或資料夾至此)", "格式", "大小", "規格 (時長 / 解析度)",
            "關鍵影格 (GOP)", "Moov 索引", "卡頓評估", "進度", "狀態"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(9, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(0, 48)
        self.table.setColumnWidth(2, 60)
        self.table.setColumnWidth(3, 85)
        self.table.setColumnWidth(4, 140)
        self.table.setColumnWidth(5, 120)
        self.table.setColumnWidth(6, 110)
        self.table.setColumnWidth(7, 150)
        self.table.setColumnWidth(8, 80)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        self.table.itemDoubleClicked.connect(self.on_table_double_click)
        self.table.itemChanged.connect(lambda item: self.update_count_label() if item.column() == 0 else None)
        main_layout.addWidget(self.table)

        # ----------------------------------------------------
        # 4. Collapsible Advanced Drawer
        # ----------------------------------------------------
        self.drawer_frame = QFrame()
        self.drawer_frame.setObjectName("DrawerFrame")
        self.drawer_frame.setVisible(False)
        drawer_vbox = QVBoxLayout(self.drawer_frame)
        drawer_vbox.setContentsMargins(14, 12, 14, 12)
        drawer_vbox.setSpacing(10)

        # Drawer Row 1: GOP Density & Quality (CQ)
        row1_layout = QHBoxLayout()
        row1_layout.setSpacing(20)

        # GOP Slider
        gop_box = QVBoxLayout()
        self.lbl_gop_slider = QLabel("關鍵影格間隔 (GOP): 1.5 秒 (推薦平衡，兼顧體積與流暢秒拖)")
        self.lbl_gop_slider.setStyleSheet("color: #d0d0d0; font-weight: bold; font-size: 12px;")
        self.slider_gop = QSlider(Qt.Orientation.Horizontal)
        self.slider_gop.setRange(10, 30)  # 1.0s to 3.0s (divided by 10)
        self.slider_gop.setValue(15)
        self.slider_gop.valueChanged.connect(self.on_gop_slider_change)
        gop_box.addWidget(self.lbl_gop_slider)
        gop_box.addWidget(self.slider_gop)
        row1_layout.addLayout(gop_box, stretch=1)

        # CQ / CRF Quality Slider
        cq_box = QVBoxLayout()
        self.lbl_cq_slider = QLabel("編碼品質 (CQ/CRF): 22 (推薦預設，視覺無損平衡)")
        self.lbl_cq_slider.setStyleSheet("color: #d0d0d0; font-weight: bold; font-size: 12px;")
        self.slider_cq = QSlider(Qt.Orientation.Horizontal)
        self.slider_cq.setRange(16, 28)
        self.slider_cq.setValue(22)
        self.slider_cq.valueChanged.connect(self.on_cq_slider_change)
        cq_box.addWidget(self.lbl_cq_slider)
        cq_box.addWidget(self.slider_cq)
        row1_layout.addLayout(cq_box, stretch=1)

        drawer_vbox.addLayout(row1_layout)

        # Drawer Row 2: Audio Strategy & Pixel Format & Output Directory
        row2_layout = QHBoxLayout()
        row2_layout.setSpacing(20)

        # Audio
        aud_box = QHBoxLayout()
        aud_label = QLabel("音訊處理策略：")
        aud_label.setStyleSheet("color: #888888; font-weight: bold; font-size: 12px;")
        self.rb_aud_copy = QRadioButton("無損原音直接複製 (Direct Copy)")
        self.rb_aud_copy.setChecked(True)
        self.rb_aud_aac = QRadioButton("轉為 AAC 192k (相容性最高)")
        self.aud_group = QButtonGroup()
        self.aud_group.addButton(self.rb_aud_copy)
        self.aud_group.addButton(self.rb_aud_aac)
        aud_box.addWidget(aud_label)
        aud_box.addWidget(self.rb_aud_copy)
        aud_box.addWidget(self.rb_aud_aac)
        row2_layout.addLayout(aud_box)

        # Pixel format checkbox
        self.chk_yuv420p = QCheckBox("強制標準 yuv420p 色彩格式 (確保 10-bit HDR / 4:4:4 相容性)")
        self.chk_yuv420p.setChecked(True)
        self.chk_yuv420p.setStyleSheet("color: #90b8de; font-weight: bold;")
        row2_layout.addWidget(self.chk_yuv420p)
        row2_layout.addStretch()

        drawer_vbox.addLayout(row2_layout)

        # Drawer Row 3: Output Directory Setting
        row3_layout = QHBoxLayout()
        out_label = QLabel("輸出目錄設定：")
        out_label.setStyleSheet("color: #888888; font-weight: bold; font-size: 12px;")
        self.rb_out_default = QRadioButton("各影片目錄下自動建立「修復_零卡頓」資料夾 (推薦)")
        self.rb_out_default.setChecked(True)
        self.rb_out_custom = QRadioButton("統一輸出至指定資料夾：")
        self.out_group = QButtonGroup()
        self.out_group.addButton(self.rb_out_default)
        self.out_group.addButton(self.rb_out_custom)

        self.lbl_custom_path = QLabel("(尚未指定)")
        self.lbl_custom_path.setStyleSheet("color: #90b8de; font-style: italic;")
        self.btn_browse_out = QPushButton("瀏覽目錄...")
        self.btn_browse_out.setEnabled(False)
        self.btn_browse_out.clicked.connect(self.on_browse_output_dir)
        self.rb_out_custom.toggled.connect(lambda chk: self.btn_browse_out.setEnabled(chk))

        row3_layout.addWidget(out_label)
        row3_layout.addWidget(self.rb_out_default)
        row3_layout.addWidget(self.rb_out_custom)
        row3_layout.addWidget(self.lbl_custom_path)
        row3_layout.addWidget(self.btn_browse_out)
        row3_layout.addStretch()

        drawer_vbox.addLayout(row3_layout)
        main_layout.addWidget(self.drawer_frame)

        # ----------------------------------------------------
        # 5. Bottom Progress Bars & Action Buttons
        # ----------------------------------------------------
        bottom_frame = QFrame()
        bottom_layout = QVBoxLayout(bottom_frame)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(6)

        p_layout = QHBoxLayout()
        self.lbl_current_task = QLabel("提示: 支援直接拖曳影片或資料夾至清單，設定完成後點擊「開始批次處理」。")
        self.lbl_current_task.setStyleSheet("color: #888888; font-size: 12px;")
        p_layout.addWidget(self.lbl_current_task)
        p_layout.addStretch()
        self.lbl_speed_eta = QLabel("")
        self.lbl_speed_eta.setStyleSheet("color: #90b8de; font-weight: bold; font-size: 12px;")
        p_layout.addWidget(self.lbl_speed_eta)
        bottom_layout.addLayout(p_layout)

        # Dual Progress Bars
        self.progress_single = QProgressBar()
        self.progress_single.setObjectName("ProgressSingle")
        self.progress_single.setFixedHeight(8)
        self.progress_single.setTextVisible(False)
        self.progress_single.setValue(0)
        bottom_layout.addWidget(self.progress_single)

        self.progress_overall = QProgressBar()
        self.progress_overall.setObjectName("ProgressOverall")
        self.progress_overall.setFixedHeight(16)
        self.progress_overall.setTextVisible(True)
        self.progress_overall.setFormat("整體進度: %p%")
        self.progress_overall.setValue(0)
        bottom_layout.addWidget(self.progress_overall)

        # Actions Row
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        self.btn_open_out = QPushButton("開啟輸出目錄")
        self.btn_open_out.setFixedHeight(36)
        self.btn_open_out.clicked.connect(self.on_open_output_folder)
        actions_layout.addWidget(self.btn_open_out)

        actions_layout.addStretch()

        self.btn_start = QPushButton("開始批次處理")
        self.btn_start.setObjectName("BtnMainExecute")
        self.btn_start.setFixedHeight(38)
        self.btn_start.setMinimumWidth(200)
        self.btn_start.clicked.connect(self.on_start_repair)
        actions_layout.addWidget(self.btn_start)

        self.btn_stop = QPushButton("中止處理")
        self.btn_stop.setFixedHeight(36)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.on_stop)
        actions_layout.addWidget(self.btn_stop)

        bottom_layout.addLayout(actions_layout)
        main_layout.addWidget(bottom_frame)

    def apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #181818;
            }
            QWidget {
                font-family: "Segoe UI", "Microsoft JhengHei", sans-serif;
                color: #cccccc;
                font-size: 12px;
            }

            /* Containers / Frames */
            #HeaderFrame, #CardOverview, #CardPresets, #CardEngine {
                background-color: #212121;
                border: 1px solid #303030;
                border-radius: 0px;
            }
            #DrawerFrame {
                background-color: #1c1c1c;
                border: 1px solid #383838;
                border-radius: 0px;
            }

            /* Push Buttons */
            QPushButton {
                background-color: #2b2b2b;
                color: #d4d4d4;
                border: 1px solid #3a3a3a;
                border-radius: 0px;
                padding: 5px 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #363636;
                border-color: #4a4a4a;
                color: #ffffff;
            }
            QPushButton:pressed {
                background-color: #1f1f1f;
                border-color: #222222;
            }
            QPushButton:disabled {
                background-color: #202020;
                color: #555555;
                border-color: #2b2b2b;
            }

            /* Main Execute Button (Studio Blue, crisp square) */
            #BtnMainExecute {
                background-color: #0e639c;
                color: #ffffff;
                font-size: 13.5px;
                font-weight: bold;
                border: 1px solid #1177bb;
                border-radius: 0px;
            }
            #BtnMainExecute:hover {
                background-color: #1177bb;
                border-color: #2b88cc;
            }
            #BtnMainExecute:pressed {
                background-color: #094770;
                border-color: #0e639c;
            }
            #BtnMainExecute:disabled {
                background-color: #222e38;
                color: #556875;
                border-color: #283a48;
            }

            /* Filter Lag Button (Subdued brick red) */
            #BtnFilterLag {
                background-color: #2a1d1d;
                border: 1px solid #5a2e2e;
                color: #e07a7a;
                font-weight: bold;
                border-radius: 0px;
            }
            #BtnFilterLag:hover {
                background-color: #3a2222;
                border-color: #7a3e3e;
                color: #f09a9a;
            }
            #BtnFilterLag:pressed {
                background-color: #1f1414;
            }

            /* Drawer Toggle Button */
            #BtnToggleDrawer {
                background-color: #242424;
                border: 1px solid #3c3c3c;
                color: #90b8de;
                font-weight: 500;
                border-radius: 0px;
            }
            #BtnToggleDrawer:hover {
                background-color: #2c2c2c;
                border-color: #0e639c;
                color: #ffffff;
            }

            /* Table Widget */
            QTableWidget {
                background-color: #1a1a1a;
                color: #dcdcdc;
                gridline-color: #262626;
                border: 1px solid #303030;
                border-radius: 0px;
                selection-background-color: #143550;
                selection-color: #ffffff;
            }
            QHeaderView::section {
                background-color: #242424;
                color: #909090;
                padding: 6px 8px;
                border: 1px solid #1a1a1a;
                font-weight: bold;
                font-size: 11px;
                border-radius: 0px;
            }

            /* Progress Bars */
            #ProgressSingle {
                background-color: #222222;
                border: 1px solid #303030;
                border-radius: 0px;
            }
            #ProgressSingle::chunk {
                background-color: #0e639c;
                border-radius: 0px;
            }
            #ProgressOverall {
                background-color: #222222;
                border: 1px solid #303030;
                border-radius: 0px;
                color: #ffffff;
                text-align: center;
                font-size: 11px;
                font-weight: bold;
            }
            #ProgressOverall::chunk {
                background-color: #1b5e20;
                border-radius: 0px;
            }

            /* Radio Buttons & Checkboxes */
            QRadioButton, QCheckBox {
                color: #cccccc;
                spacing: 6px;
                font-size: 12px;
            }
            QRadioButton::indicator {
                width: 13px;
                height: 13px;
                background-color: #252525;
                border: 1px solid #444444;
                border-radius: 0px;
            }
            QRadioButton::indicator:checked {
                background-color: #0e639c;
                border: 2px solid #3880b0;
                border-radius: 0px;
            }
            QCheckBox::indicator {
                width: 13px;
                height: 13px;
                background-color: #252525;
                border: 1px solid #444444;
                border-radius: 0px;
            }
            QCheckBox::indicator:checked {
                background-color: #0e639c;
                border: 1px solid #3880b0;
                border-radius: 0px;
            }

            /* Combo Boxes */
            QComboBox {
                background-color: #242424;
                color: #d4d4d4;
                border: 1px solid #3a3a3a;
                border-radius: 0px;
                padding: 4px 8px;
                min-width: 140px;
            }
            QComboBox:hover {
                border-color: #4a4a4a;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background-color: #242424;
                color: #d4d4d4;
                border: 1px solid #3a3a3a;
                border-radius: 0px;
                selection-background-color: #0e639c;
                selection-color: #ffffff;
            }

            /* Sliders */
            QSlider::groove:horizontal {
                height: 4px;
                background: #333333;
                border-radius: 0px;
            }
            QSlider::sub-page:horizontal {
                background: #0e639c;
                border-radius: 0px;
            }
            QSlider::handle:horizontal {
                background: #b0b0b0;
                border: 1px solid #0e639c;
                width: 12px;
                margin-top: -6px;
                margin-bottom: -6px;
                border-radius: 0px;
            }
            QSlider::handle:horizontal:hover {
                background: #ffffff;
            }

            /* ScrollBars */
            QScrollBar:vertical {
                background: #181818;
                width: 10px;
                margin: 0px;
                border-radius: 0px;
            }
            QScrollBar::handle:vertical {
                background: #2e2e2e;
                min-height: 20px;
                border-radius: 0px;
            }
            QScrollBar::handle:vertical:hover {
                background: #3e3e3e;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar:horizontal {
                background: #181818;
                height: 10px;
                margin: 0px;
                border-radius: 0px;
            }
            QScrollBar::handle:horizontal {
                background: #2e2e2e;
                min-width: 20px;
                border-radius: 0px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #3e3e3e;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0px;
            }

            /* Context Menu */
            QMenu {
                background-color: #222222;
                border: 1px solid #383838;
                border-radius: 0px;
                padding: 4px 0px;
            }
            QMenu::item {
                color: #cccccc;
                padding: 6px 20px;
            }
            QMenu::item:selected {
                background-color: #0e639c;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background-color: #333333;
                margin: 4px 8px;
            }
        """)

    # Sliders feedback
    def on_gop_slider_change(self, val):
        sec = val / 10.0
        if sec <= 1.0:
            desc = "極速秒拖，隨點隨到"
        elif sec <= 1.8:
            desc = "推薦平衡，兼顧體積與流暢"
        else:
            desc = "體積更小，偶有微延遲"
        self.lbl_gop_slider.setText(f"關鍵影格間隔 (GOP): {sec:.1f} 秒 ({desc})")

    def on_cq_slider_change(self, val):
        if val <= 19:
            desc = "極致高畫質 (高保真無損)"
        elif val <= 23:
            desc = "推薦預設 (視覺無損平衡)"
        else:
            desc = "大幅節省檔案體積"
        self.lbl_cq_slider.setText(f"編碼品質 (CQ/CRF): {val} ({desc})")

    def toggle_drawer(self):
        self.drawer_visible = not self.drawer_visible
        self.drawer_frame.setVisible(self.drawer_visible)
        if self.drawer_visible:
            self.btn_toggle_drawer.setText("進階編碼設定 ▲")
        else:
            self.btn_toggle_drawer.setText("進階編碼設定 ▼")

    # Drag and Drop
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
        video_exts = ('.mp4', '.ts', '.mkv', '.mov', '.avi', '.flv', '.wmv')
        files_to_add = []
        for p in paths:
            if os.path.isfile(p) and p.lower().endswith(video_exts):
                files_to_add.append(p)
            elif os.path.isdir(p):
                for root, _, files in os.walk(p):
                    for f in files:
                        if f.lower().endswith(video_exts):
                            files_to_add.append(os.path.join(root, f))
        if files_to_add:
            self.add_videos_to_table(files_to_add)

    def on_add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "選取欲處理的影片檔案 (可多選)", "",
            "常見影片檔案 (*.mp4 *.ts *.mkv *.mov *.avi *.flv *.wmv);;所有檔案 (*.*)"
        )
        if files:
            self.add_videos_to_table(files)

    def on_add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "選取影片資料夾")
        if folder:
            video_exts = ('.mp4', '.ts', '.mkv', '.mov', '.avi', '.flv', '.wmv')
            files = []
            for root, _, filenames in os.walk(folder):
                for f in filenames:
                    if f.lower().endswith(video_exts):
                        files.append(os.path.join(root, f))
            if files:
                self.add_videos_to_table(files)
            else:
                QMessageBox.information(self, "提示", "所選資料夾中未找到支援格式的影片檔案。")

    def add_videos_to_table(self, file_paths):
        existing_paths = set()
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 1)
            if item:
                existing_paths.add(item.data(Qt.ItemDataRole.UserRole))

        for p in file_paths:
            p = os.path.abspath(p)
            if p in existing_paths:
                continue

            fname = os.path.basename(p)
            ext = os.path.splitext(fname)[1].upper().replace('.', '')
            size_mb = os.path.getsize(p) / (1024 * 1024)
            size_str = f"{size_mb / 1024:.2f} GB" if size_mb >= 1024 else f"{size_mb:.1f} MB"

            row = self.table.rowCount()
            self.table.insertRow(row)

            # 0. Checkbox
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            chk_item.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(row, 0, chk_item)

            # 1. Filename
            name_item = QTableWidgetItem(fname)
            name_item.setData(Qt.ItemDataRole.UserRole, p)
            name_item.setToolTip(p)
            self.table.setItem(row, 1, name_item)

            self.table.setItem(row, 2, QTableWidgetItem(ext))
            self.table.setItem(row, 3, QTableWidgetItem(size_str))
            self.table.setItem(row, 4, QTableWidgetItem("-"))
            self.table.setItem(row, 5, QTableWidgetItem("-"))
            self.table.setItem(row, 6, QTableWidgetItem("-"))
            self.table.setItem(row, 7, QTableWidgetItem("未檢測"))
            self.table.setItem(row, 8, QTableWidgetItem("0%"))
            self.table.setItem(row, 9, QTableWidgetItem("待處理"))

            existing_paths.add(p)

        self.update_count_label()

    def update_count_label(self):
        total = self.table.rowCount()
        checked = 0
        danger = 0
        for r in range(total):
            item = self.table.item(r, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                checked += 1
            risk_item = self.table.item(r, 7)
            if risk_item and "高風險" in risk_item.text():
                danger += 1
        self.lbl_stat_line.setText(f"總計: {total} 部 ｜ 已選: {checked} 部 ｜ 卡頓風險: {danger} 部")

    def set_all_checked(self, state):
        chk = Qt.CheckState.Checked if state else Qt.CheckState.Unchecked
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                item.setCheckState(chk)
        self.update_count_label()

    def select_only_lagging(self):
        selected_cnt = 0
        for r in range(self.table.rowCount()):
            fmt_item = self.table.item(r, 2)
            risk_item = self.table.item(r, 7)
            is_ts = fmt_item and fmt_item.text().upper() == 'TS'
            is_risky = risk_item and ("高風險" in risk_item.text() or "中風險" in risk_item.text())

            chk_item = self.table.item(r, 0)
            if chk_item:
                if is_ts or is_risky:
                    chk_item.setCheckState(Qt.CheckState.Checked)
                    selected_cnt += 1
                else:
                    chk_item.setCheckState(Qt.CheckState.Unchecked)

        self.update_count_label()
        if selected_cnt == 0:
            QMessageBox.information(self, "提示", "目前清單中未發現卡頓或 TS 格式影片。\n(若尚未檢測，可先點選「檢測勾選項目」)")

    def on_remove_selected(self):
        selected_rows = sorted(set(idx.row() for idx in self.table.selectedIndexes()), reverse=True)
        for r in selected_rows:
            self.table.removeRow(r)
        self.update_count_label()

    def on_clear_all(self):
        self.table.setRowCount(0)
        self.cached_results.clear()
        self.update_count_label()

    def on_browse_output_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "選取輸出目錄")
        if folder:
            self.lbl_custom_path.setText(folder)

    def on_table_double_click(self, item):
        row = item.row()
        item_path = self.table.item(row, 1)
        if item_path:
            p = item_path.data(Qt.ItemDataRole.UserRole)
            if p and os.path.exists(p):
                try:
                    os.startfile(p)
                except Exception:
                    pass

    def show_context_menu(self, pos):
        menu = QMenu(self)
        row = self.table.rowAt(pos.y())
        if row >= 0:
            p = self.table.item(row, 1).data(Qt.ItemDataRole.UserRole)

            act_play = QAction("使用預設播放器播放", self)
            act_play.triggered.connect(lambda: os.startfile(p) if os.path.exists(p) else None)

            act_open_dir = QAction("在檔案總管中開啟所在位置", self)
            act_open_dir.triggered.connect(lambda: subprocess.run(['explorer', '/select,', os.path.normpath(p)]))

            act_check = QAction("勾選此項目", self)
            act_check.triggered.connect(lambda: self.table.item(row, 0).setCheckState(Qt.CheckState.Checked))

            act_uncheck = QAction("取消勾選此項目", self)
            act_uncheck.triggered.connect(lambda: self.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked))

            act_remove = QAction("自清單移除", self)
            act_remove.triggered.connect(lambda: self.table.removeRow(row))

            menu.addAction(act_play)
            menu.addAction(act_open_dir)
            menu.addSeparator()
            menu.addAction(act_check)
            menu.addAction(act_uncheck)
            menu.addSeparator()
            menu.addAction(act_remove)
            menu.exec(QCursor.pos())

    # Detection Workflow
    def on_start_detect(self):
        if not self.ffmpeg_path:
            QMessageBox.critical(self, "錯誤", "未找到 FFmpeg 核心工具。")
            return

        tasks = []
        for r in range(self.table.rowCount()):
            item_chk = self.table.item(r, 0)
            if item_chk and item_chk.checkState() == Qt.CheckState.Checked:
                p = self.table.item(r, 1).data(Qt.ItemDataRole.UserRole)
                tasks.append((r, p))

        if not tasks:
            QMessageBox.warning(self, "提示", "請先勾選欲檢測的影片。")
            return

        self.btn_detect.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress_single.setValue(0)
        self.progress_overall.setValue(0)

        self.analysis_worker = AnalysisWorker(self.ffmpeg_path, tasks)
        self.analysis_worker.sig_progress.connect(self.on_detect_progress)
        self.analysis_worker.sig_row_done.connect(self.on_detect_row_done)
        self.analysis_worker.sig_all_done.connect(self.on_detect_all_done)
        self.analysis_worker.start()

    def on_detect_progress(self, cur, total, fname):
        self.lbl_current_task.setText(f"正在多點抽樣檢測關鍵影格 ({cur}/{total}): {fname}")
        pct = int((cur / total) * 100)
        self.progress_overall.setValue(pct)

    def on_detect_row_done(self, row, info):
        self.cached_results[info['filepath']] = info

        # Duration & Res
        res_str = f"{info['duration']} ｜ {info['resolution']}@{info['fps']}fps"
        self.table.setItem(row, 4, QTableWidgetItem(res_str))

        # GOP
        gop_str = f"最大 {info['max_gop']}s"
        item_gop = QTableWidgetItem(gop_str)
        if info['max_gop'] >= 5.0:
            item_gop.setForeground(QBrush(QColor('#f87171')))
        elif info['max_gop'] >= 3.0:
            item_gop.setForeground(QBrush(QColor('#fbbf24')))
        else:
            item_gop.setForeground(QBrush(QColor('#4ade80')))
        self.table.setItem(row, 5, item_gop)

        # Moov
        self.table.setItem(row, 6, QTableWidgetItem(info['moov']))

        # Risk Level
        item_lvl = QTableWidgetItem(info['level'])
        if "高風險" in info['level']:
            item_lvl.setForeground(QBrush(QColor('#f87171')))
        elif "中風險" in info['level']:
            item_lvl.setForeground(QBrush(QColor('#fbbf24')))
        else:
            item_lvl.setForeground(QBrush(QColor('#4ade80')))
        self.table.setItem(row, 7, item_lvl)

        self.table.setItem(row, 9, QTableWidgetItem("已檢測"))
        self.update_count_label()

    def on_detect_all_done(self):
        self.lbl_current_task.setText("檢測診斷已完成。")
        self.lbl_speed_eta.setText("")
        self.progress_single.setValue(100)
        self.progress_overall.setValue(100)
        self.btn_detect.setEnabled(True)
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.update_count_label()
        QMessageBox.information(
            self, "完成",
            "所選影片之關鍵影格與卡頓分析已完成。\n可點擊「產生診斷報告」閱讀詳細數據，或點擊「篩選卡頓影片」一鍵勾選處理。"
        )

    # Repair Workflow
    def on_start_repair(self):
        if not self.ffmpeg_path:
            QMessageBox.critical(self, "錯誤", "未找到 FFmpeg 核心工具。")
            return

        tasks = []
        for r in range(self.table.rowCount()):
            item_chk = self.table.item(r, 0)
            if item_chk and item_chk.checkState() == Qt.CheckState.Checked:
                p = self.table.item(r, 1).data(Qt.ItemDataRole.UserRole)
                fps_val = '25.0'
                if p in self.cached_results:
                    fps_val = self.cached_results[p].get('fps', '25.0')
                tasks.append((r, p, fps_val))

        if not tasks:
            QMessageBox.warning(self, "提示", "請先勾選欲修復的影片。")
            return

        mode = 'repair' if self.rb_nvenc.isChecked() else ('remux_ts' if self.rb_remux.isChecked() else 'detect')
        if mode == 'detect':
            self.on_start_detect()
            return

        out_mode = 'custom' if self.rb_out_custom.isChecked() else 'default_subfolder'
        custom_dir = self.lbl_custom_path.text() if out_mode == 'custom' else ""
        if out_mode == 'custom' and (not custom_dir or not os.path.exists(custom_dir)):
            QMessageBox.warning(self, "提示", "請先點選「瀏覽目錄」指定統一輸出資料夾。")
            return

        encoder_choice = self.combo_encoder.currentData()
        concurrency = self.combo_concurrency.currentData()
        gop_sec = self.slider_gop.value() / 10.0
        cq = self.slider_cq.value()
        audio_mode = 'copy' if self.rb_aud_copy.isChecked() else 'aac'

        self.btn_detect.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress_single.setValue(0)
        self.progress_overall.setValue(0)

        self.repair_worker = RepairWorker(
            self.ffmpeg_path, tasks, mode, out_mode, custom_dir,
            encoder_choice=encoder_choice,
            gop_sec=gop_sec,
            cq=cq,
            audio_mode=audio_mode,
            concurrency=concurrency
        )
        self.repair_worker.sig_overall.connect(self.on_repair_overall)
        self.repair_worker.sig_item_progress.connect(self.on_repair_item_progress)
        self.repair_worker.sig_item_done.connect(self.on_repair_item_done)
        self.repair_worker.sig_all_done.connect(self.on_repair_all_done)
        self.repair_worker.start()

    def on_repair_overall(self, cur, total):
        pct = int((cur / total) * 100)
        self.progress_overall.setValue(pct)
        self.lbl_current_task.setText(f"正在批次處理中 ({cur}/{total} 部完成)...")

    def on_repair_item_progress(self, row, pct, speed_str, eta_str):
        self.progress_single.setValue(int(pct))
        self.table.setItem(row, 8, QTableWidgetItem(f"{pct:.1f}%"))
        self.table.setItem(row, 9, QTableWidgetItem("轉碼中..."))
        self.lbl_speed_eta.setText(f"速度: {speed_str}x ｜ 剩餘時間: {eta_str}")

    def on_repair_item_done(self, row, success, size_mb, outpath, msg):
        item_status = QTableWidgetItem(msg)
        if success:
            item_status.setForeground(QBrush(QColor('#4ade80')))
            self.table.setItem(row, 8, QTableWidgetItem("100%"))
        else:
            item_status.setForeground(QBrush(QColor('#f87171')))
        self.table.setItem(row, 9, item_status)

    def on_repair_all_done(self, success_cnt, fail_cnt, last_out_dir):
        self.last_repaired_dir = last_out_dir
        self.lbl_current_task.setText(f"全部處理完畢。成功: {success_cnt} 部，失敗: {fail_cnt} 部")
        self.lbl_speed_eta.setText("")
        self.progress_single.setValue(100)
        self.progress_overall.setValue(100)
        self.btn_detect.setEnabled(True)
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

        reply = QMessageBox.information(
            self, "處理完成",
            f"所有勾選的影片已完成修復。\n\n成功: {success_cnt} 部\n失敗: {fail_cnt} 部\n\n輸出目錄:\n{last_out_dir}\n\n是否立即開啟輸出資料夾？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if reply == QMessageBox.StandardButton.Yes and os.path.exists(last_out_dir):
            os.startfile(last_out_dir)

    def on_open_output_folder(self):
        target = self.last_repaired_dir
        if not target or not os.path.exists(target):
            if self.rb_out_custom.isChecked() and os.path.exists(self.lbl_custom_path.text()):
                target = self.lbl_custom_path.text()
            else:
                target = os.path.join(os.path.abspath('.'), '修復_零卡頓')

        if os.path.exists(target):
            os.startfile(target)
        else:
            QMessageBox.information(self, "提示", f"輸出資料夾尚未建立：\n{target}")

    def on_stop(self):
        if self.analysis_worker and self.analysis_worker.isRunning():
            self.analysis_worker.cancel()
        if self.repair_worker and self.repair_worker.isRunning():
            self.repair_worker.cancel()
        self.lbl_current_task.setText("已中斷操作。")
        self.lbl_speed_eta.setText("")
        self.btn_detect.setEnabled(True)
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def on_view_report(self):
        if not self.cached_results:
            analyzed = []
            for r in range(self.table.rowCount()):
                p = self.table.item(r, 1).data(Qt.ItemDataRole.UserRole)
                lvl = self.table.item(r, 7).text()
                if "未檢測" not in lvl and p in self.cached_results:
                    analyzed.append(self.cached_results[p])

            if not analyzed:
                QMessageBox.information(self, "提示", "尚未有任何影片完成檢測分析。請先勾選影片並點擊「檢測勾選項目」。")
                return

        results = list(self.cached_results.values())
        report_path = os.path.join(os.path.abspath('.'), "影片卡頓檢測報告.html")
        generate_html_report(results, report_path)
        try:
            webbrowser.open(f"file:///{os.path.normpath(report_path)}")
        except Exception as e:
            QMessageBox.critical(self, "錯誤", f"無法開啟瀏覽器: {e}")


def generate_html_report(results, report_path):
    total = len(results)
    high_count = sum(1 for r in results if '高風險' in r['level'])
    med_count = sum(1 for r in results if '中風險' in r['level'])
    low_count = sum(1 for r in results if '良好' in r['level'])

    rows_html = []
    for r in results:
        badge_class = 'badge-danger' if '高風險' in r['level'] else ('badge-warning' if '中風險' in r['level'] else 'badge-success')
        gop_style = 'color: #f87171; font-weight: bold;' if r['max_gop'] >= 5.0 else ('color: #fbbf24;' if r['max_gop'] >= 3.0 else 'color: #4ade80;')

        rows_html.append(f"""
        <tr>
            <td class="file-name" title="{r['filename']}">{r['filename']}</td>
            <td><span class="badge {badge_class}">{r['level']}</span></td>
            <td>{r['format']}</td>
            <td>{r['resolution']} @ {r['fps']}fps</td>
            <td>{r['duration']}</td>
            <td>{r['bitrate']}</td>
            <td style="{gop_style}">{r['max_gop']} 秒</td>
            <td>{r['moov']}</td>
            <td><span class="issues-text">{r['issues']}</span></td>
        </tr>
        """)

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>VideoFixer Pro | 影片卡頓檢測報告</title>
    <style>
        :root {{
            --bg-color: #181818;
            --card-bg: #222222;
            --text-main: #d4d4d4;
            --text-sub: #888888;
            --accent: #0e639c;
            --danger: #f87171;
            --warning: #fbbf24;
            --success: #4ade80;
            --border: #333333;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            margin: 0;
            padding: 24px;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        header {{
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--border);
        }}
        h1 {{
            font-size: 20px;
            margin: 0 0 8px 0;
            color: #ffffff;
            font-weight: 600;
        }}
        .time-tag {{
            font-size: 13px;
            color: var(--text-sub);
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 12px;
            margin-bottom: 20px;
        }}
        .stat-card {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 0px;
            padding: 14px;
            text-align: center;
        }}
        .stat-card .num {{
            font-size: 26px;
            font-weight: bold;
            margin: 4px 0;
        }}
        .stat-card .label {{
            font-size: 12px;
            color: var(--text-sub);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: var(--card-bg);
            border-radius: 0px;
            overflow: hidden;
            border: 1px solid var(--border);
        }}
        th, td {{
            padding: 10px 12px;
            text-align: left;
            border-bottom: 1px solid var(--border);
            font-size: 13px;
        }}
        th {{
            background: #282828;
            color: var(--text-sub);
            font-weight: 600;
            border-radius: 0px;
        }}
        tr:hover {{
            background: #2a2a2a;
        }}
        .file-name {{
            font-weight: 500;
            max-width: 320px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .badge {{
            display: inline-block;
            padding: 3px 6px;
            border-radius: 0px;
            font-size: 11.5px;
            font-weight: 600;
        }}
        .badge-danger {{ background: #2a1d1d; color: var(--danger); border: 1px solid #5a2e2e; }}
        .badge-warning {{ background: #2a2418; color: var(--warning); border: 1px solid #5a4820; }}
        .badge-success {{ background: #1c261e; color: var(--success); border: 1px solid #2a402d; }}
        .guide-box {{
            margin-top: 20px;
            padding: 14px;
            background: #202020;
            border-left: 3px solid var(--accent);
            border-radius: 0px;
            font-size: 13px;
            line-height: 1.6;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>VIDEOFIXER PRO | 影片卡頓檢測報告</h1>
            <div class="time-tag">檢測時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
        </header>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="label">總檢測影片數</div>
                <div class="num">{total}</div>
            </div>
            <div class="stat-card">
                <div class="label">高風險 (卡頓嚴重)</div>
                <div class="num" style="color: var(--danger);">{high_count}</div>
            </div>
            <div class="stat-card">
                <div class="label">中風險 (偶有延遲)</div>
                <div class="num" style="color: var(--warning);">{med_count}</div>
            </div>
            <div class="stat-card">
                <div class="label">良好無卡頓</div>
                <div class="num" style="color: var(--success);">{low_count}</div>
            </div>
        </div>

        <table>
            <thead>
                <tr>
                    <th>檔案名稱</th>
                    <th>健康狀態</th>
                    <th>格式</th>
                    <th>解析度 / 幀率</th>
                    <th>時長</th>
                    <th>碼率</th>
                    <th>關鍵影格間隔 (GOP)</th>
                    <th>Moov 索引位置</th>
                    <th>檢測出的問題</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows_html)}
            </tbody>
        </table>

        <div class="guide-box">
            <strong>解讀與修復建議：</strong><br>
            1. <strong>TS 格式檔案</strong>：缺乏隨機存取索引表，拖動進度條容易黑畫面或轉圈。建議使用「TS 封裝轉換」進行秒速無損轉裝。<br>
            2. <strong>GOP &gt; 5 秒</strong>：關鍵影格過少，播放器跳轉時必須重算數百張畫面才能顯示第一幀。使用「零卡頓修復 (1.5秒關鍵影格)」可徹底治本解決。
        </div>
    </div>
</body>
</html>
"""
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = VideoFixerApp()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
