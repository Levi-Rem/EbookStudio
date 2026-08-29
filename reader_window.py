# -*- coding: utf-8 -*-
"""
reader_window.py — 阅读器窗口

特性: 章节目录 + 按章懒加载正文 / 全文搜索(跳转+高亮) / 书签 /
      字号与夜间模式记忆(QSettings) / 阅读进度记忆(SQLite)
"""
import html as html_mod

from PySide6.QtCore import Qt, QSettings, QTimer, QUrl
from PySide6.QtGui import (
    QColor, QFont, QImage, QTextCharFormat, QTextCursor, QTextDocument)
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QTextBrowser, QTextEdit, QToolBar,
    QTabWidget, QVBoxLayout, QWidget, QSizePolicy)

from reader import BookReader

MAX_HIGHLIGHT = 500


class EpubBrowser(QTextBrowser):
    """支持从 EPUB zip 中加载相对图片资源；按键交给 ReaderWindow 统一处理"""

    def __init__(self, book, window=None, parent=None):
        super().__init__(parent)
        self.book = book
        self.window_ref = window
        self.setOpenExternalLinks(False)

    def loadResource(self, rtype, url):
        if rtype == QTextDocument.ResourceType.ImageResource:
            path = QUrl(url).path().lstrip('/')
            data = self.book.epub_resource(path)
            if data:
                img = QImage()
                img.loadFromData(data)
                return img
        return super().loadResource(rtype, url)

    def keyPressEvent(self, ev):
        if self.window_ref is not None and self.window_ref.handle_reader_key(ev):
            ev.accept()
            return
        super().keyPressEvent(ev)


class ReaderWindow(QDialog):
    def __init__(self, book_row, lib, parent=None):
        super().__init__(parent)
        self.row = book_row
        self.lib = lib
        self.book = BookReader()
        self.cur = 0
        self.pending_scroll = None      # 待恢复的滚动位置比例
        self.settings = QSettings('EbookStudio', 'reader')
        self.setWindowTitle(f'阅读 — {book_row["title"]}')
        self.resize(1250, 800)
        self.setModal(False)
        # QDialog 默认无最大化按钮；开启以支持最大化/全屏切换
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self._build_ui()
        try:
            self.book.open(book_row['path'])
        except Exception as e:
            self.browser.setHtml(f'<h2>无法打开</h2><p>{html_mod.escape(str(e))}</p>')
            self.list.setEnabled(False)
            return
        for (t,) in self.book.chapters:
            self.list.addItem(t)
        self._load_font_settings()
        # 恢复上次阅读位置
        last_ch, last_scroll = self.lib.get_position(book_row['path'])
        if 0 < last_ch < len(self.book.chapters):
            self.list.setCurrentRow(last_ch)
            self.pending_scroll = last_scroll
        elif self.list.count():
            self.list.setCurrentRow(0)

    # ---------------- UI ----------------
    def _build_ui(self):
        root = QHBoxLayout(self)

        # 左侧章节目录
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(4, 4, 0, 4)
        lv.addWidget(QLabel('目录'))
        self.list = QListWidget()
        self.list.setFixedWidth(240)
        self.list.currentRowChanged.connect(self._on_chapter)
        lv.addWidget(self.list)
        root.addWidget(left)

        # 中间正文
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 4, 0, 4)
        bar = QToolBar()
        bar.setMovable(False)
        bar.addAction('◄ 上一章', self._prev)
        bar.addAction('下一章 ►', self._next)
        bar.addSeparator()
        bar.addAction('A+', self._zoom_in)
        bar.addAction('A-', self._zoom_out)
        self.width_action = bar.addAction('宽度', self._cycle_width)
        self.width_action.setToolTip('切换正文显示宽度')
        self.dark_action = bar.addAction('夜间', self._toggle_dark)
        self.fullscreen_action = bar.addAction('⛶ 全屏', self._toggle_fullscreen)
        self.fullscreen_action.setShortcut('F11')
        bar.addSeparator()
        a_search = bar.addAction('🔍 搜索', self._toggle_search)
        a_search.setShortcut('Ctrl+F')
        a_bm = bar.addAction('🔖 书签', self._toggle_bookmarks)
        a_bm.setShortcut('Ctrl+B')
        bar.addWidget(QLabel('  '))
        self.pos_lab = QLabel(' ')
        bar.addWidget(self.pos_lab)
        cv.addWidget(bar)
        # 正文容器: stretch-browser-stretch，限制 browser 宽度时自动居中
        bh = QHBoxLayout()
        bh.setContentsMargins(0, 0, 0, 0)
        bh.addStretch(1)
        self.browser = EpubBrowser(self.book, self)
        self.browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        bh.addWidget(self.browser, 1)
        bh.addStretch(1)
        cv.addLayout(bh)
        self.browser.setFont(QFont('Microsoft YaHei',
                                   self.settings.value('fontsize', 12, int)))
        self._apply_theme(self.settings.value('dark', False, bool))
        self._apply_width(self.settings.value('width_idx', 0, int))
        root.addWidget(center, 1)

        # 右侧: 搜索 / 书签 面板
        self.side = QTabWidget()
        self.side.setFixedWidth(340)
        self.side.setVisible(False)
        self._build_search_tab()
        self._build_bookmark_tab()
        root.addWidget(self.side)
        # 快捷键已由工具栏 QAction.setShortcut 注册，勿重复绑定（否则切换两次等于没切）

    def _build_search_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText('输入关键词，回车搜索全书')
        self.search_edit.returnPressed.connect(self._do_search)
        btn = QPushButton('搜索')
        btn.clicked.connect(self._do_search)
        row.addWidget(self.search_edit)
        row.addWidget(btn)
        v.addLayout(row)
        self.search_hits = QListWidget()
        self.search_hits.itemClicked.connect(self._goto_hit)
        v.addWidget(self.search_hits)
        self.search_stat = QLabel(' ')
        v.addWidget(self.search_stat)
        self.side.addTab(w, '搜索')

    def _build_bookmark_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        self.bm_list = QListWidget()
        self.bm_list.itemDoubleClicked.connect(self._goto_bookmark)
        v.addWidget(self.bm_list)
        row = QHBoxLayout()
        b_add = QPushButton('添加书签')
        b_add.clicked.connect(self._add_bookmark)
        b_del = QPushButton('删除选中')
        b_del.clicked.connect(self._del_bookmark)
        row.addWidget(b_add)
        row.addWidget(b_del)
        v.addLayout(row)
        self.side.addTab(w, '书签')

    # ---------------- 章节渲染 ----------------
    def _render(self):
        title, body = self.book.get_chapter(self.cur)
        # TXT 的 get_chapter 已构造安全 HTML（标签由代码生成），无需再转义
        self.browser.setHtml(body)
        self.pos_lab.setText(f'{self.cur + 1} / {len(self.book.chapters)}')
        self._save_position()

    def _on_chapter(self, row):
        if row < 0:
            return
        self.cur = row
        self._render()
        if self.pending_scroll is not None:
            self._apply_pending_scroll()
        else:
            self.browser.verticalScrollBar().setValue(0)

    def _apply_pending_scroll(self, attempt=0):
        sb = self.browser.verticalScrollBar()
        if sb.maximum() > 0 or attempt >= 10:
            sb.setValue(int(sb.maximum() * (self.pending_scroll or 0)))
            self.pending_scroll = None
        else:
            QTimer.singleShot(60, lambda: self._apply_pending_scroll(attempt + 1))

    def _prev(self):
        if self.cur > 0:
            self.list.setCurrentRow(self.cur - 1)

    def _next(self):
        if self.cur < len(self.book.chapters) - 1:
            self.list.setCurrentRow(self.cur + 1)

    # ---------------- 字号 / 主题（记忆） ----------------
    def _load_font_settings(self):
        pass  # 字号与主题已在 _build_ui 中从 QSettings 恢复

    def _zoom_in(self):
        f = self.browser.font()
        f.setPointSize(f.pointSize() + 1)
        self.browser.setFont(f)
        self.settings.setValue('fontsize', f.pointSize())

    def _zoom_out(self):
        f = self.browser.font()
        if f.pointSize() > 8:
            f.setPointSize(f.pointSize() - 1)
        self.browser.setFont(f)
        self.settings.setValue('fontsize', f.pointSize())

    def _apply_theme(self, dark):
        if dark:
            self.browser.setStyleSheet(
                'QTextBrowser{padding:18px 36px;background:#1e1e1e;color:#c8c8c8;}')
        else:
            self.browser.setStyleSheet('QTextBrowser{padding:18px 36px;}')
        self.settings.setValue('dark', dark)

    def _toggle_dark(self):
        cur_dark = self.settings.value('dark', False, bool)
        self._apply_theme(not cur_dark)

    # ---------------- 正文宽度 / 全屏 ----------------
    WIDTH_PRESETS = [0, 1400, 1200, 1000, 800, 640]   # 0 = 不限（全宽）

    def _apply_width(self, idx):
        """按档位限制正文控件宽度并居中；0 表示跟随窗口全宽"""
        idx = max(0, min(idx, len(self.WIDTH_PRESETS) - 1))
        w = self.WIDTH_PRESETS[idx]
        if w > 0:
            self.browser.setFixedWidth(w)
        else:
            self.browser.setMinimumWidth(0)
            self.browser.setMaximumWidth(0x00FFFFFF)
        label = '全宽' if w == 0 else f'{w}px'
        self.width_action.setText(f'宽度: {label}')
        self.width_idx = idx
        self.settings.setValue('width_idx', idx)

    def _cycle_width(self):
        self._apply_width((getattr(self, 'width_idx', 0) + 1)
                          % len(self.WIDTH_PRESETS))

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ---------------- 搜索 ----------------
    def _toggle_search(self):
        show = not self.side.isVisible() or self.side.currentIndex() != 0
        self.side.setVisible(show)
        if show:
            self.side.setCurrentIndex(0)
            self.search_edit.setFocus()

    def _do_search(self):
        q = self.search_edit.text().strip()
        self.search_hits.clear()
        if not q:
            return
        self.search_stat.setText('搜索中…')
        hits = self.book.search(q)
        last_idx, occ = -1, 0
        for idx, title, snippet in hits:
            occ = occ + 1 if idx == last_idx else 0
            last_idx = idx
            item = QListWidgetItem(f'[{title}]\n  …{snippet}…')
            item.setData(Qt.UserRole, (idx, q, occ))
            self.search_hits.addItem(item)
        total = len(hits)
        self.search_stat.setText(f'共 {total} 处（每章最多显示 3 处）'
                                 if total else '未找到')

    def _goto_hit(self, item):
        idx, q, occurrence = item.data(Qt.UserRole)
        self.pending_scroll = None
        if idx == self.cur:
            # 已在本章：setCurrentRow 不会触发信号，直接高亮定位
            self._highlight(q, occurrence)
        else:
            self.list.setCurrentRow(idx)
            QTimer.singleShot(80, lambda: self._highlight(q, occurrence))

    def _highlight(self, q, occurrence=0):
        """高亮本章全部命中，视口定位到第 occurrence 处（0 起）"""
        doc = self.browser.document()
        sels = []
        cur = QTextCursor(doc)
        while len(sels) < MAX_HIGHLIGHT:
            cur = doc.find(q, cur)
            if cur.isNull():
                break
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(QColor('#ffe066'))
            sel.format.setForeground(QColor('#000000'))
            sel.cursor = cur
            sels.append(sel)
        self.browser.setExtraSelections(sels)
        if sels:
            target = sels[min(occurrence, len(sels) - 1)].cursor
            self.browser.setTextCursor(target)
            sb = self.browser.verticalScrollBar()
            sb.setValue(max(0, sb.value() - sb.pageStep() // 3))

    # ---------------- 书签 ----------------
    def _toggle_bookmarks(self):
        show = not self.side.isVisible() or self.side.currentIndex() != 1
        self.side.setVisible(show)
        if show:
            self.side.setCurrentIndex(1)
            self._refresh_bookmarks()

    def _scroll_frac(self):
        sb = self.browser.verticalScrollBar()
        return (sb.value() / sb.maximum()) if sb.maximum() else 0.0

    def _add_bookmark(self):
        title = self.book.chapters[self.cur][0]
        frac = self._scroll_frac()
        label = f'{title}  @{frac * 100:.0f}%'
        self.lib.add_bookmark(self.row['path'], self.cur, frac, label)
        self._refresh_bookmarks()

    def _refresh_bookmarks(self):
        self.bm_list.clear()
        for bm in self.lib.bookmarks(self.row['path']):
            item = QListWidgetItem(f'{bm["label"]}\n    {bm["created"]}')
            item.setData(Qt.UserRole, bm)
            self.bm_list.addItem(item)

    def _goto_bookmark(self, item):
        bm = item.data(Qt.UserRole)
        if not bm or not (0 <= bm['chapter'] < len(self.book.chapters)):
            return
        if bm['chapter'] == self.cur:
            # 已在本章：直接恢复滚动位置
            sb = self.browser.verticalScrollBar()
            sb.setValue(int(sb.maximum() * (bm['scroll'] or 0)))
            return
        self.pending_scroll = bm['scroll']
        self.list.setCurrentRow(bm['chapter'])

    def _del_bookmark(self):
        item = self.bm_list.currentItem()
        if item:
            self.lib.remove_bookmark(item.data(Qt.UserRole)['id'])
            self._refresh_bookmarks()

    # ---------------- 阅读进度 ----------------
    def _save_position(self):
        try:
            title = self.book.chapters[self.cur][0] \
                if self.cur < len(self.book.chapters) else ''
            self.lib.save_position(self.row['path'], self.cur,
                                   self._scroll_frac(),
                                   self.row.get('title', ''), title)
        except Exception:
            pass

    def closeEvent(self, ev):
        self._save_position()
        self.book.close()          # 释放 EPUB zip 句柄（Windows 上占用会阻止文件移动/删除）
        super().closeEvent(ev)

    # ---------------- 按键翻页 ----------------
    def handle_reader_key(self, ev):
        """返回 True 表示按键已消费。
        PageUp/PageDown: 上一页/下一页（章节末自动进入下一章）
        ←/→: 上一章/下一章   ↑/↓: 上滚/下滚一行"""
        key = ev.key()
        sb = self.browser.verticalScrollBar()
        if key == Qt.Key_Left:
            self._prev()
            return True
        if key == Qt.Key_Right:
            self._next()
            return True
        if key == Qt.Key_PageDown:
            if sb.value() >= sb.maximum():
                self._next()
            else:
                sb.setValue(min(sb.value() + int(sb.pageStep() * 0.92),
                                sb.maximum()))
            return True
        if key == Qt.Key_PageUp:
            if sb.value() <= 0:
                if self.cur > 0:
                    self.pending_scroll = 1.0
                    self._prev()
            else:
                sb.setValue(max(sb.value() - int(sb.pageStep() * 0.92), 0))
            return True
        if key == Qt.Key_Down:
            sb.setValue(min(sb.value() + max(sb.singleStep(), 4), sb.maximum()))
            return True
        if key == Qt.Key_Up:
            sb.setValue(max(sb.value() - max(sb.singleStep(), 4), 0))
            return True
        return False

    def keyPressEvent(self, ev):
        # QDialog 默认 Esc=reject 关窗；全屏时 Esc 应先退出全屏
        if ev.key() == Qt.Key_Escape and self.isFullScreen():
            self.showNormal()
            ev.accept()
            return
        if self.handle_reader_key(ev):
            ev.accept()
            return
        super().keyPressEvent(ev)
