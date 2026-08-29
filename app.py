# -*- coding: utf-8 -*-
"""
EbookStudio — 迷你 Calibre：书库管理 + 高速转换 + 内置阅读

启动: python app.py
"""
import hashlib, os, sys, tempfile

from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, QProcess, Qt, QSortFilterProxyModel, QUrl)
from PySide6.QtGui import (
    QAction, QColor, QFont, QFontMetrics, QPainter, QPixmap, QDesktopServices)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QSplitter, QTableWidget, QTableWidgetItem, QTableView,
    QToolBar, QVBoxLayout, QWidget)

import library, metadata
from reader_window import ReaderWindow

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, 'data')
COVER_DIR = os.path.join(APP_DIR, 'covers')
DB_PATH = os.path.join(DATA_DIR, 'library.db')
WORKER = os.path.join(APP_DIR, 'convert_worker.py')
EXTS = ('.txt', '.epub', '.mobi', '.azw3')
COLUMNS = ['书名', '作者', '格式', '大小', '加入时间', '路径']


def human_size(n):
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return f'{n:.0f} {u}' if u == 'B' else f'{n:.1f} {u}'
        n /= 1024
    return f'{n:.1f} TB'


def default_cover(title, author):
    """没有封面的书：用 QPainter 画一张文字封面"""
    pm = QPixmap(400, 560)
    pm.fill(QColor('#3d6b9e'))
    p = QPainter(pm)
    p.setPen(QColor('white'))
    f = QFont('Microsoft YaHei', 26)
    f.setBold(True)
    p.setFont(f)
    fm = QFontMetrics(f)
    words, lines, cur = list(title), [], ''
    for w in words:
        if fm.horizontalAdvance(cur + w) > 340 and cur:
            lines.append(cur)
            cur = w
        else:
            cur += w
    lines.append(cur)
    if len(lines) > 3:
        lines = lines[:3]
    y = 150
    for ln in lines:
        p.drawText(30, y, ln)
        y += 44
    f2 = QFont('Microsoft YaHei', 14)
    p.setFont(f2)
    p.drawText(30, 500, (author or ' ')[:20])
    p.end()
    return pm


class LibraryModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows = []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        key = {'书名': 'title', '作者': 'author', '格式': 'format',
               '大小': 'size', '加入时间': 'added', '路径': 'path'}[
                   COLUMNS[index.column()]]
        if role == Qt.DisplayRole:
            if key == 'size':
                return human_size(r.get('size', 0))
            return str(r.get(key, ''))
        return None

    def headerData(self, s, o, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and o == Qt.Horizontal:
            return COLUMNS[s]
        return None


class ConvertDialog(QDialog):
    def __init__(self, titles, parent=None):
        super().__init__(parent)
        self.setWindowTitle('转换设置')
        self.resize(430, 230)
        form = QFormLayout(self)
        form.addRow(QLabel(f'已选 {len(titles)} 本：' +
                           '、'.join(titles[:3]) + ('…' if len(titles) > 3 else '')))
        self.fmt = {}
        row = QHBoxLayout()
        for name in ('epub', 'mobi', 'azw3'):
            cb = QCheckBox(name)
            cb.setChecked(True)
            self.fmt[name] = cb
            row.addWidget(cb)
        form.addRow('目标格式:', row)
        self.outdir = QLineEdit()
        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                               QDialogButtonBox.StandardButton.Cancel)
        btn.accepted.connect(self.accept)
        btn.rejected.connect(self.reject)
        lab = QLabel('<a href="#">选择目录</a>')
        lab.linkActivated.connect(self._pick)
        row2 = QHBoxLayout()
        row2.addWidget(self.outdir)
        row2.addWidget(lab)
        form.addRow('输出目录:', row2)
        form.addRow(btn)

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, '输出目录')
        if d:
            self.outdir.setText(d)

    def value(self):
        fmts = [k for k, cb in self.fmt.items() if cb.isChecked()]
        return fmts, self.outdir.text().strip()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(COVER_DIR, exist_ok=True)
        self.lib = library.Library(DB_PATH)
        self.model = LibraryModel()
        self.proxy = QSortFilterProxyModel()
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proc = None
        self._build_ui()
        self.refresh()

    # ---------- UI ----------
    def _build_ui(self):
        self.setWindowTitle('EbookStudio — 书库')
        self.resize(1200, 720)

        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)
        a_scan = QAction('扫描目录导入', self)
        a_scan.triggered.connect(self.scan_dir)
        a_add = QAction('添加文件', self)
        a_add.triggered.connect(self.add_files)
        a_read = QAction('阅读', self)
        a_read.triggered.connect(self.read_book)
        a_conv = QAction('转换…', self)
        a_conv.triggered.connect(self.convert_selected)
        a_open = QAction('用系统应用打开', self)
        a_open.triggered.connect(self.open_external)
        a_del = QAction('移出书库', self)
        a_del.triggered.connect(self.remove_selected)
        a_hist = QAction('阅读历史', self)
        a_hist.triggered.connect(self.show_history)
        for a in (a_scan, a_add):
            tb.addAction(a)
        tb.addSeparator()
        for a in (a_read, a_conv, a_open, a_del, a_hist):
            tb.addAction(a)
        self.read_action, self.conv_action = a_read, a_conv

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(6, 6, 6, 6)
        self.search = QLineEdit()
        self.search.setPlaceholderText('🔍 搜索书名 / 作者')
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        v.addWidget(self.search)

        split = QSplitter()
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setEditTriggers(QTableView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setShowGrid(False)
        self.table.doubleClicked.connect(lambda *_: self.read_book())
        self.table.selectionModel().selectionChanged.connect(
            lambda *_: self.update_detail())
        split.addWidget(self.table)

        self.detail_layout = QVBoxLayout()
        self.detail_layout.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        detail_holder = QWidget()
        detail_holder.setLayout(self.detail_layout)
        detail_holder.setMinimumWidth(280)
        detail_holder.setStyleSheet('QWidget{padding:0px;}')
        split.addWidget(detail_holder)
        split.setSizes([820, 330])
        v.addWidget(split)
        self.setCentralWidget(central)
        self.statusBar().showMessage('就绪')

    def _set_detail(self, widget):
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ---------- 数据 ----------
    def refresh(self):
        q = self.search.text()
        self.model.set_rows(self.lib.all(q))

    def selected_rows(self):
        idxs = self.table.selectionModel().selectedRows()
        return [self.model.rows[self.proxy.mapToSource(i).row()] for i in idxs]

    def save_cover(self, probe):
        cover = probe.get('cover')
        if not cover:
            return ''
        h = hashlib.md5(cover).hexdigest()[:16]
        path = os.path.join(COVER_DIR, h + '.img')
        with open(path, 'wb') as f:
            f.write(cover)
        return path

    def import_paths(self, paths):
        n = 0
        for p in paths:
            if not os.path.splitext(p)[1].lower() in EXTS or not os.path.isfile(p):
                continue
            probe = metadata.probe(p)
            cover = self.save_cover(probe)
            if self.lib.add(probe, os.path.abspath(p), cover):
                n += 1
        self.refresh()
        return n

    # ---------- 动作 ----------
    def scan_dir(self):
        d = QFileDialog.getExistingDirectory(self, '选择要扫描的目录')
        if not d:
            return
        paths = []
        for root, _dirs, files in os.walk(d):
            for f in files:
                if os.path.splitext(f)[1].lower() in EXTS:
                    paths.append(os.path.join(root, f))
        n = self.import_paths(paths)
        self.statusBar().showMessage(f'扫描完成：新导入 {n} 本（共 {len(paths)} 个文件）')

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, '添加书籍', '', '电子书 (*.txt *.epub *.mobi *.azw3)')
        if files:
            n = self.import_paths(files)
            self.statusBar().showMessage(f'新导入 {n} 本')

    def update_detail(self):
        rows = self.selected_rows()
        if not rows:
            return
        r = rows[0]
        if r.get('cover_file') and os.path.exists(r['cover_file']):
            pm = QPixmap(r['cover_file'])
        else:
            pm = default_cover(r['title'], r.get('author', ''))
        pm = pm.scaledToWidth(200, Qt.SmoothTransformation)
        lab = QLabel()
        lab.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        lab.setPixmap(pm)
        info = QLabel(
            f'<div style="margin-top:8px"><b style="font-size:15px">'
            f'{r["title"]}</b><br><span style="color:#666">'
            f'{r.get("author") or "未知作者"}</span><br><br>'
            f'格式：{r["format"]}<br>大小：{human_size(r["size"])}<br>'
            f'加入：{r["added"]}</div>')
        info.setWordWrap(True)
        info.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self._set_detail(None)
        self.detail_layout.addWidget(lab)
        self.detail_layout.addWidget(info)
        self.detail_layout.addStretch(1)

    def read_book(self):
        rows = self.selected_rows()
        if not rows:
            QMessageBox.information(self, '阅读', '请先选择一本书')
            return
        try:
            w = ReaderWindow(rows[0], self.lib, self)
            w.show()
        except Exception as e:
            QMessageBox.warning(self, '阅读', f'打开失败: {e}')

    def open_external(self):
        rows = self.selected_rows()
        if rows:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(os.path.abspath(rows[0]['path'])))

    def remove_selected(self):
        rows = self.selected_rows()
        if not rows:
            return
        if QMessageBox.question(
                self, '移出书库',
                f'从书库移除 {len(rows)} 本？（不删除磁盘文件）') != \
                QMessageBox.StandardButton.Yes:
            return
        for r in rows:
            self.lib.remove(r['id'])
        self.refresh()

    def show_history(self):
        rows = self.lib.reading_log(300)
        dlg = QDialog(self)
        dlg.setWindowTitle('阅读历史')
        dlg.resize(760, 540)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        v = QVBoxLayout(dlg)
        tw = QTableWidget(len(rows), 4)
        tw.setHorizontalHeaderLabels(['时间', '书名', '章节序号', '章节标题'])
        for i, r in enumerate(rows):
            for j, key in enumerate(('ts', 'title', 'chapter', 'chapter_title')):
                tw.setItem(i, j, QTableWidgetItem(str(r.get(key, ''))))
        tw.setColumnWidth(0, 160)
        tw.setColumnWidth(1, 170)
        tw.setColumnWidth(2, 80)
        tw.setEditTriggers(QTableWidget.NoEditTriggers)
        tw.verticalHeader().setVisible(False)
        tw.setShowGrid(False)
        tw.doubleClicked.connect(lambda idx: self._open_from_log(rows[idx.row()]))
        v.addWidget(tw)
        v.addWidget(QLabel('双击条目打开对应书籍（自动恢复阅读进度）'))
        self._history_dlg = dlg     # 持引用防被 GC
        dlg.destroyed.connect(lambda *_: setattr(self, '_history_dlg', None))
        dlg.show()

    def _open_from_log(self, log_row):
        row = next((x for x in self.lib.all()
                    if x['path'] == log_row['book_path']), None)
        if not row:
            QMessageBox.information(self, '阅读历史', '该书已不在书库中')
            return
        try:
            ReaderWindow(row, self.lib, self).show()
        except Exception as e:
            QMessageBox.warning(self, '阅读历史', f'打开失败: {e}')

    # ---------- 转换 ----------
    def convert_selected(self):
        rows = self.selected_rows()
        txt_rows = [r for r in rows
                    if os.path.splitext(r['path'])[1].lower() == '.txt']
        if not txt_rows:
            QMessageBox.information(
                self, '转换',
                '转换源仅支持 TXT（epub/mobi/azw3 无需再转换）。\n'
                '如需其他格式互转请选中对应 TXT 源文件。')
            return
        if len(txt_rows) < len(rows):
            skipped = len(rows) - len(txt_rows)
            if QMessageBox.question(
                    self, '转换',
                    f'已忽略 {skipped} 本非 TXT 文件，继续转换 {len(txt_rows)} 本？'
                    ) != QMessageBox.StandardButton.Yes:
                return
        rows = txt_rows
        dlg = ConvertDialog([r['title'] for r in rows], self)
        dlg.outdir.setText(os.path.dirname(rows[0]['path']))
        if dlg.exec() != QDialog.Accepted:
            return
        fmts, outdir = dlg.value()
        if not fmts:
            return
        jobs = [{'src': r['path'], 'outdir': outdir, 'formats': fmts,
                 'title': r['title'], 'author': r.get('author', '')}
                for r in rows]
        self._start_jobs(jobs, outdir)

    def _start_jobs(self, jobs, outdir):
        if self.proc is not None:
            QMessageBox.warning(self, '转换', '已有转换任务在进行中')
            return
        fd, jf = tempfile.mkstemp(suffix='.json')
        import json
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(jobs, f, ensure_ascii=False)
        self._proc_saw_error = False
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(APP_DIR)
        self.proc.readyReadStandardOutput.connect(
            lambda: self._on_proc_out())
        self.proc.finished.connect(
            lambda *_: self._on_proc_done(jf, outdir))
        self.proc.errorOccurred.connect(
            lambda err: self._on_proc_error(jf, err))
        self.conv_action.setEnabled(False)
        self.statusBar().showMessage('转换中… 0%')
        self.proc.start(sys.executable, ['-X', 'utf8', WORKER, jf])

    def _on_proc_out(self):
        data = bytes(self.proc.readAllStandardOutput()).decode('utf-8', 'replace')
        for line in data.splitlines():
            if line.startswith('PROGRESS'):
                _, i, n, *rest = line.split()
                self.statusBar().showMessage(
                    f'转换中… {i}/{n}  {" ".join(rest)}')
            elif line.startswith('ERROR'):
                self._proc_saw_error = True
                self.statusBar().showMessage(line[:120])

    def _on_proc_error(self, jf, err):
        QMessageBox.critical(self, '转换', f'转换进程异常（{err}），任务中止')
        self.conv_action.setEnabled(True)
        try:
            os.remove(jf)
        except OSError:
            pass
        self.proc = None

    def _on_proc_done(self, jf, outdir):
        data = bytes(self.proc.readAllStandardOutput()).decode('utf-8', 'replace')
        for line in data.splitlines():
            if line.startswith('ERROR'):
                self._proc_saw_error = True
        ok = not self._proc_saw_error
        self.conv_action.setEnabled(True)
        try:
            os.remove(jf)
        except OSError:
            pass
        self.proc = None
        self.statusBar().showMessage('转换完成 ✓' if ok else
                                     '转换完成（部分失败，见状态栏）')
        if outdir:
            paths = [os.path.join(outdir, f) for f in os.listdir(outdir)
                     if os.path.splitext(f)[1].lower() in EXTS]
            self.import_paths(paths)


    def closeEvent(self, ev):
        # Qt 销毁子窗口不触发其 closeEvent，主动让阅读窗口保存进度并释放句柄
        for w in self.findChildren(ReaderWindow):
            try:
                w._save_position()
                w.book.close()
            except Exception:
                pass
        super().closeEvent(ev)


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
