# -*- coding: utf-8 -*-
"""
convert_worker.py — 转换子进程 worker

独立进程执行转换：大书转换的内存随进程结束释放，UI 永不卡死。
转换时自动生成文字封面并嵌入 epub/mobi/azw3。
用法: python convert_worker.py <jobs.json>
stdout 输出: PROGRESS <i>/<n> <书名> / DONE / ERROR <msg>
"""
import json, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import txt2ebook


def generate_cover(title, author, out_png):
    """离屏 Qt 绘制文字封面（书名 + 作者 + 简单装饰）"""
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import Qt
    from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication,
                               QImage, QPainter, QPen)

    app = QGuiApplication.instance() or QGuiApplication([])
    W, H = 600, 840
    img = QImage(W, H, QImage.Format.Format_ARGB32)
    img.fill(QColor('#35567a'))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor('#d9c98a'), 3))
    p.drawRect(18, 18, W - 36, H - 36)
    p.setPen(QColor('white'))
    f = QFont('Microsoft YaHei', 40)
    f.setBold(True)
    p.setFont(f)
    fm = QFontMetrics(f)
    lines, cur = [], ''
    for chx in title:
        if fm.horizontalAdvance(cur + chx) > W - 120 and cur:
            lines.append(cur)
            cur = chx
        else:
            cur += chx
    lines.append(cur)
    if len(lines) > 4:
        lines = lines[:4] + ['…']
    y = 200
    for ln in lines:
        p.drawText(60, y, ln)
        y += 62
    f2 = QFont('Microsoft YaHei', 22)
    p.setFont(f2)
    p.setPen(QColor('#d9c98a'))
    p.drawText(60, H - 130, f'著：{author or "佚名"}')
    p.setPen(QPen(QColor('#d9c98a'), 2))
    p.drawLine(60, H - 160, W - 60, H - 160)
    p.end()
    img.save(out_png, 'PNG')


def run(jobs):
    ok_all = True
    for i, job in enumerate(jobs, 1):
        title = job['title']
        try:
            if os.path.splitext(job['src'])[1].lower() != '.txt':
                print(f'ERROR {title}: 仅支持 TXT 源文件，已跳过', flush=True)
                ok_all = False
                continue
            print(f'PROGRESS {i}/{len(jobs)} {title}', flush=True)
            t, author, chapters = txt2ebook.parse_book(job['src'])
            t = job.get('title') or t
            author = job.get('author') or author
            out_dir = job['outdir']
            os.makedirs(out_dir, exist_ok=True)
            fmts = job['formats']
            cover_png = None
            try:
                fd, cover_png = tempfile.mkstemp(suffix='.png')
                os.close(fd)
                generate_cover(t, author, cover_png)
            except Exception:
                cover_png = None
            if 'epub' in fmts:
                txt2ebook.build_epub((t, author, 'zh'), chapters,
                                     os.path.join(out_dir, t + '.epub'),
                                     cover_path=cover_png)
            rest = [f for f in fmts if f != 'epub']
            if rest:
                ep = os.path.join(out_dir, t + '.epub')
                if not os.path.exists(ep):
                    txt2ebook.build_epub((t, author, 'zh'), chapters, ep,
                                         cover_path=cover_png)
                converter = txt2ebook.find_converter()
                if converter and converter[0] == 'kindlegen':
                    for out, good, err in txt2ebook.make_mobi_azw3(
                            ep, out_dir, rest, converter):
                        if not good:
                            print(f'ERROR {out}: {err}', flush=True)
                            ok_all = False
                else:
                    print('ERROR 未找到 kindlegen.exe，无法生成 ' + ','.join(rest))
                    ok_all = False
            if cover_png:
                try:
                    os.remove(cover_png)
                except OSError:
                    pass
        except Exception as e:
            print(f'ERROR {title}: {e}', flush=True)
            ok_all = False
    print('DONE' if ok_all else 'PARTIAL', flush=True)


if __name__ == '__main__':
    with open(sys.argv[1], encoding='utf-8') as f:
        run(json.load(f))
