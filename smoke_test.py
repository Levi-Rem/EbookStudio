# -*- coding: utf-8 -*-
"""smoke_test.py — 无界面逻辑测试（QT_QPA_PLATFORM=offscreen）"""
import os, sys, tempfile, time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import metadata, library, reader, convert_worker, glob as _glob

# 提前创建 Qt 实例（worker 的封面绘制会复用它；真实应用中 worker 是独立进程）
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
_qapp = QApplication.instance() or QApplication([])

TMP = tempfile.mkdtemp(prefix='es_test_')
TXT_DIR = r'D:\txt'
SRC = {k: _glob.glob(os.path.join(TXT_DIR, f'*{k}*.txt'))[0]
       for k in ('神墓', '圣墟', '长生界')}
results = []


def check(name, cond, extra=''):
    results.append((name, bool(cond), extra))
    print(f'[{"PASS" if cond else "FAIL"}] {name} {extra}')


# 1. 元数据探测：epub / mobi / azw3 各测一本
ep = os.path.join(TXT_DIR, '神墓.epub')
mo = os.path.join(TXT_DIR, '神墓.mobi')
az = os.path.join(TXT_DIR, '圣墟.azw3')
for p in (ep, mo, az):
    info = metadata.probe(p)
    check(f'probe {os.path.basename(p)}',
          info['title'] and info['format'],
          f"title={info['title']} author={info['author']} "
          f"cover={'有' if info['cover'] else '无'}")

# 2. 书库
db = os.path.join(TMP, 'lib.db')
lib = library.Library(db)
lib.add(metadata.probe(ep), ep)
lib.add(metadata.probe(mo), mo)
lib.add(metadata.probe(ep), ep)  # 重复路径应去重
check('书库去重', len(lib.all()) == 2)
check('书库搜索', len(lib.all('神墓')) == 2)

# 3. 阅读内核：四种格式切章
t0 = time.time()
r = reader.BookReader().open(SRC['神墓'])
titles = [t for (t,) in r.chapters]
check('TXT 阅读切章', len(r.chapters) == 767 and '第一卷 走出神墓 第一章 远古神墓' in titles[1],
      f'{len(r.chapters)} 章, {time.time()-t0:.1f}s')
_, html = r.get_chapter(2)
check('TXT 按章取正文', html.startswith('<h') and '<p>' in html)

r = reader.BookReader().open(ep)
check('EPUB 阅读切章', len(r.chapters) >= 250, f'{len(r.chapters)} 章')
_, html = r.get_chapter(1)
check('EPUB 按章取正文', '<html' in html.lower() or '<h2' in html.lower())

t0 = time.time()
r = reader.BookReader().open(mo)
check('MOBI 阅读切章', len(r.chapters) >= 200,
      f'{len(r.chapters)} 章, {time.time()-t0:.1f}s')
_, html = r.get_chapter(3)
check('MOBI 按章取正文', len(html) > 200)

t0 = time.time()
r = reader.BookReader().open(az)
check('AZW3 阅读切章', len(r.chapters) >= 200,
      f'{len(r.chapters)} 章, {time.time()-t0:.1f}s')

# 内存约束抽查：单章正文必须远小于整本
# 4. 转换 worker（子进程逻辑直接调用）
worker_out = os.path.join(TMP, 'conv')
jobs = [{'src': os.path.join(TXT_DIR, '《长生界》（精校全本）作者：辰东.txt'),
         'outdir': worker_out, 'formats': ['epub', 'mobi', 'azw3'],
         'title': None, 'author': None}]
t0 = time.time()
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    convert_worker.run(jobs)
out = buf.getvalue()
files = os.listdir(worker_out)
check('转换 worker 三格式', all(f'长生界.{e}' in files for e in ('epub', 'mobi', 'azw3')),
      f'{time.time()-t0:.0f}s, 输出: {files}')
check('worker 进度输出', 'PROGRESS 1/1' in out and 'DONE' in out)

# 5. worker 产物可回读
rr = reader.BookReader().open(os.path.join(worker_out, '长生界.azw3'))
check('自产 azw3 可回读', len(rr.chapters) >= 600, f'{len(rr.chapters)} 章')

# 6. 目录页精确切分（mobi/azw3 内嵌 TOC 锚点）
r = reader.BookReader().open(mo)
check('MOBI 精确切分', len(r.chapters) >= 700,
      f'{len(r.chapters)} 章; 首3={[t for (t,) in r.chapters[:3]]}')
r = reader.BookReader().open(az)
check('AZW3 精确切分', len(r.chapters) >= 1600,
      f'{len(r.chapters)} 章; 首3={[t for (t,) in r.chapters[:3]]}')

# 7. 全文搜索
r = reader.BookReader().open(SRC['神墓'])
hits = r.search('辰南', cap=20)
check('TXT 书内搜索', len(hits) >= 10, f'{len(hits)} 处, 首条: {hits[0][:2] if hits else "-"}')
r = reader.BookReader().open(mo)
hits = r.search('辰南', cap=20)
check('MOBI 书内搜索', len(hits) >= 5, f'{len(hits)} 处')

# 8. 书签与阅读进度
lib2 = library.Library(os.path.join(TMP, 'lib2.db'))
p = os.path.abspath(ep)
lib2.add(metadata.probe(ep), p)
lib2.save_position(p, 12, 0.37)
ch, sc = lib2.get_position(p)
check('进度存取', (ch, sc) == (12, 0.37), f'{ch},{sc}')
lib2.add_bookmark(p, 5, 0.5, '第五章 @50%')
lib2.add_bookmark(p, 9, 0.1, '第九章 @10%')
bms = lib2.bookmarks(p)
check('书签增查', len(bms) == 2 and bms[0]['label'] == '第九章 @10%')
lib2.remove_bookmark(bms[0]['id'])
check('书签删除', len(lib2.bookmarks(p)) == 1)

# 9. 旧库迁移（无新列的库应自动补列）
import sqlite3
old_db = os.path.join(TMP, 'old.db')
c = sqlite3.connect(old_db)
c.execute('CREATE TABLE books(id INTEGER PRIMARY KEY, title TEXT, author TEXT, '
          'path TEXT UNIQUE, format TEXT, size INTEGER, cover_file TEXT, added TEXT)')
c.execute("INSERT INTO books(title,path) VALUES('旧书','x.txt')")
c.commit(); c.close()
lib3 = library.Library(old_db)
check('旧库迁移', 'last_chapter' in {r[1] for r in lib3.conn.execute('PRAGMA table_info(books)')})

# 10. 转换产物内嵌封面
info = metadata.probe(os.path.join(worker_out, '长生界.epub'))
check('epub 内嵌封面', info['cover'] is not None,
      f"{len(info['cover']) if info['cover'] else 0} 字节")
info = metadata.probe(os.path.join(worker_out, '长生界.azw3'))
check('azw3 内嵌封面', info['cover'] is not None,
      f"{len(info['cover']) if info['cover'] else 0} 字节")

# 11. 阅读历史（同章去重、跨章新增）
lib4 = library.Library(os.path.join(TMP, 'lib4.db'))
lib4.save_position('/a.txt', 1, 0.0, '书A', '第一章')
lib4.save_position('/a.txt', 1, 0.5, '书A', '第一章')
lib4.save_position('/a.txt', 2, 0.0, '书A', '第二章')
log = lib4.reading_log()
check('历史记录', len(log) == 2 and log[0]['chapter'] == 2 and
      log[0]['chapter_title'] == '第二章', f'{len(log)} 条')

# 12. 按键翻页（离屏模拟）
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QKeyEvent
from PySide6.QtCore import Qt, QEvent
from reader_window import ReaderWindow
qapp = QApplication.instance() or QApplication([])
rw = ReaderWindow({'path': p, 'title': '神墓'}, lib2)
rw.show()
qapp.processEvents()
sb = rw.browser.verticalScrollBar()
v0, c0 = sb.value(), rw.cur
rw.handle_reader_key(QKeyEvent(QEvent.KeyPress, Qt.Key_Down,
                               Qt.KeyboardModifier.NoModifier))
check('↓ 下滚一行', sb.value() > v0, f'{v0}->{sb.value()}')
rw.handle_reader_key(QKeyEvent(QEvent.KeyPress, Qt.Key_PageDown,
                               Qt.KeyboardModifier.NoModifier))
check('PageDown 翻页', sb.value() > v0, f'{v0}->{sb.value()}')
rw.handle_reader_key(QKeyEvent(QEvent.KeyPress, Qt.Key_Right,
                               Qt.KeyboardModifier.NoModifier))
check('→ 下一章', rw.cur == c0 + 1, f'{c0}->{rw.cur}')
rw.handle_reader_key(QKeyEvent(QEvent.KeyPress, Qt.Key_Left,
                               Qt.KeyboardModifier.NoModifier))
check('← 上一章', rw.cur == c0)
sb.setValue(sb.maximum())
rw.handle_reader_key(QKeyEvent(QEvent.KeyPress, Qt.Key_PageDown,
                               Qt.KeyboardModifier.NoModifier))
check('章末 PageDown 进下一章', rw.cur == c0 + 1, f'cur={rw.cur}')

# 13. TXT 正文不被转义（修复回归：正文应含 <p> 标签而非字面文本）
rw2 = ReaderWindow({'path': SRC['神墓'], 'title': '神墓'}, lib2)
rw2.show()
qapp.processEvents()
plain = rw2.browser.toPlainText()
check('TXT 正文无转义残留', '<p>' not in plain and '<h2>' not in plain
      and len(plain) > 50, f'{len(plain)} 字')

# 14. 同章书签跳转生效
rw2.close()
rw3 = ReaderWindow({'path': SRC['神墓'], 'title': '神墓'}, lib2)
rw3.show()
qapp.processEvents()
lib2.add_bookmark(SRC['神墓'], rw3.cur, 0.9, '测试同章跳转')
rw3._refresh_bookmarks()
n0 = rw3.bm_list.count()
rw3._goto_bookmark(rw3.bm_list.item(0))
check('同章书签跳转', rw3.bm_list.count() == n0, '书签列表不丢')

# 15. 移出书库级联清理
lib5 = library.Library(os.path.join(TMP, 'lib5.db'))
lib5.add(metadata.probe(ep), ep)
lib5.save_position(ep, 3, 0.5, '神墓', '第三章')
lib5.add_bookmark(ep, 1, 0.2, 'bm')
bid = lib5.all()[0]['id']
lib5.remove(bid)
check('移出级联清理', not lib5.bookmarks(ep) and not lib5.reading_log())

# 16. worker 拒绝非 txt 源
buf2 = io.StringIO()
with contextlib.redirect_stdout(buf2):
    convert_worker.run([{'src': ep, 'outdir': TMP, 'formats': ['epub'],
                         'title': '神墓epub', 'author': ''}])
check('worker 拒绝非txt', 'ERROR' in buf2.getvalue())

# 17. BookReader.close 释放句柄后可重复打开
r5 = reader.BookReader().open(ep)
r5.close()
r5b = reader.BookReader().open(ep)
check('close 后可重开', len(r5b.chapters) > 100, f'{len(r5b.chapters)} 章')
r5b.close()

# 18. 表头排序（SortRole 数值排序，大小列不得按字符串序）
import app as appmod
from PySide6.QtCore import Qt
m = appmod.LibraryModel()
m.set_rows([{'title': 'a', 'author': '', 'format': '', 'size': 900, 'added': '', 'path': ''},
            {'title': 'b', 'author': '', 'format': '', 'size': 1200, 'added': '', 'path': ''},
            {'title': 'c', 'author': '', 'format': '', 'size': 700, 'added': '', 'path': ''}])
from PySide6.QtCore import QSortFilterProxyModel
px = QSortFilterProxyModel()
px.setSourceModel(m)
px.setSortRole(appmod.SORT_ROLE)             # 与 app.py 一致
px.sort(3, Qt.SortOrder.AscendingOrder)     # 大小列
vals = [px.data(px.index(i, 3), Qt.ItemDataRole.DisplayRole) for i in range(3)]
check('大小列数值排序', vals == ['700 B', '900 B', '1.2 KB'], str(vals))

# 19. 正文宽度档位 / 全屏切换
rw4 = ReaderWindow({'path': SRC['神墓'], 'title': '神墓'}, lib2)
rw4.show()
qapp.processEvents()
rw4._apply_width(3)     # 1000px
check('正文宽度档位', rw4.browser.width() <= 1000 and '1000' in rw4.width_action.text(),
      rw4.width_action.text())
rw4._cycle_width()
check('宽度循环+记忆', rw4.settings.value('width_idx', -1, int) == 4,
      f"idx={rw4.settings.value('width_idx', -1, int)}")
rw4._apply_width(0)     # 恢复全宽
check('全宽恢复', rw4.browser.maximumWidth() >= 0x00FFFFFF)
rw4.close()

print()
fails = [r for r in results if not r[1]]
print(f'===== {len(results)-len(fails)}/{len(results)} 通过 =====')
sys.exit(1 if fails else 0)
