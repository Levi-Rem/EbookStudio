# -*- coding: utf-8 -*-
"""
txt2ebook.py — 把 TXT 小说转换为 EPUB / MOBI / AZW3

用法:
    python txt2ebook.py <文件.txt 或 目录> [选项]

选项:
    -o, --outdir DIR     输出目录（默认与输入文件相同）
    -f, --formats LIST   输出格式，逗号分隔: epub,mobi,azw3（默认 epub）
    -a, --author NAME    作者名（默认从文件名识别）
    -t, --title NAME     书名（默认从文件名识别）
    --lang LANG          语言（默认 zh）

实现说明:
    * EPUB 由本脚本用纯标准库生成: 按章节拆分 XHTML、生成 NCX+NAV 双目录、
      卷/章分级标题、章节分页。速度与文件大小成线性关系。
    * MOBI / AZW3 委托外部转换器（Calibre ebook-convert 或 kindlegen）从
      生成的 EPUB 转换，自动探测可用转换器。
"""
import argparse, os, re, struct, subprocess, sys, time, uuid, datetime, zipfile
from xml.sax.saxutils import escape

# ---------------- 章节识别 ----------------
NUM = r'[0-9０-９零一二三四五六七八九十百千万两]+'
RE_VOL = re.compile(rf'^\s{{0,4}}第{NUM}[卷部](?!分)\s*.{{0,30}}$')
RE_CH = re.compile(rf'^\s{{0,4}}第{NUM}[章集回]\s*.{{0,40}}$')
RE_SP = re.compile(r'^\s{0,4}(?:楔子|序章|序言|引子|后记|尾声|终章|番外|结局).{0,40}$')

def detect_encoding(path):
    raw = open(path, 'rb').read(65536)
    for enc in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return 'gb18030'

def parse_book(path):
    """返回 (书名, 作者, chapters)；chapters: [(level, 标题, [段落...]), ...]"""
    enc = detect_encoding(path)
    text = open(path, encoding=enc, errors='replace').read()

    name = os.path.splitext(os.path.basename(path))[0]
    m = re.match(r'《(.+?)》', name)
    title = m.group(1) if m else name
    am = re.search(r'作者[:：\-]\s*(\S+)', name)
    author = am.group(1) if am else '未知'

    chapters = []
    cur = None
    pre = []

    def flush_pre():
        if any(l.strip() for l in pre):
            chapters.append((2, '前言', [l.strip() for l in pre if l.strip()]))

    for line in text.splitlines():
        if RE_VOL.match(line):
            flush_pre(); pre = []
            cur = (1, line.strip(), [])
            chapters.append(cur)
        elif RE_CH.match(line) or RE_SP.match(line):
            flush_pre(); pre = []
            cur = (2, line.strip(), [])
            chapters.append(cur)
        elif not line.strip():
            continue
        elif cur is not None:
            cur[2].append(line.strip())
        else:
            pre.append(line)
    flush_pre()
    return title, author, chapters

# ---------------- EPUB 生成 ----------------
CSS = """body{line-height:1.6;margin:5% 6%;}
h1{font-size:1.4em;text-align:center;page-break-before:always;margin:1.6em 0 1em;}
h2{font-size:1.2em;text-align:center;page-break-before:always;margin:1.6em 0 1em;}
p{text-indent:2em;margin:0.2em 0;}
.titlepage{text-align:center;margin-top:35%;}
.titlepage .bt{font-size:1.8em;font-weight:bold;margin:0.4em 0;}
.titlepage .zz{font-size:1.1em;color:#444;}
"""

XHTML = ('<?xml version="1.0" encoding="utf-8"?>\n'
         '<!DOCTYPE html>\n'
         '<html xmlns="http://www.w3.org/1999/xhtml" '
         'xmlns:epub="http://www.idpf.org/2007/ops">\n'
         '<head><title>{t}</title>'
         '<link rel="stylesheet" type="text/css" href="{css}"/></head>\n'
         '<body>\n{b}\n</body></html>\n')

def build_epub(meta, chapters, out_path, cover_path=None):
    book_id = 'urn:uuid:' + str(uuid.uuid4())
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    title, author, lang = meta

    z = zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED)
    z.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip',
               compress_type=zipfile.ZIP_STORED)
    z.writestr('META-INF/container.xml',
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        ' <rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles>\n</container>\n')
    z.writestr('OEBPS/style.css', CSS)

    # 封面（可选）：自动识别 png / jpeg
    cover_items, cover_meta = [], ''
    if cover_path and os.path.exists(cover_path):
        cover = open(cover_path, 'rb').read()
        if cover[:8] == b'\x89PNG\r\n\x1a\n':
            ext, mtype = 'png', 'image/png'
        elif cover[:3] == b'\xff\xd8\xff':
            ext, mtype = 'jpg', 'image/jpeg'
        else:
            cover = None
        if cover:
            z.writestr(f'OEBPS/images/cover.{ext}', cover)
            z.writestr('OEBPS/cover.xhtml',
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<!DOCTYPE html>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
                '<title>cover</title></head>\n'
                '<body style="margin:0;padding:0;text-align:center;">'
                f'<div><img src="images/cover.{ext}" alt="cover" '
                'style="max-width:100%;"/></div></body></html>\n')
            cover_items = [
                f'<item id="cover-image" href="images/cover.{ext}" '
                f'media-type="{mtype}" properties="cover-image"/>',
                '<item id="coverpage" href="cover.xhtml" '
                'media-type="application/xhtml+xml"/>']
            cover_meta = '<meta name="cover" content="cover-image"/>\n        '

    # 扉页
    z.writestr('OEBPS/text/title.xhtml', XHTML.format(
        t=escape(title), css='../style.css',
        b=f'<div class="titlepage"><p class="bt">{escape(title)}</p>'
          f'<p class="zz">{escape(author)}</p></div>'))

    # 章节
    items = []  # (id, href, 标题)
    for i, (level, ctitle, paras) in enumerate(chapters, 1):
        cid, href = f'c{i:04d}', f'text/chap-{i:04d}.xhtml'
        tag = 'h1' if level == 1 else 'h2'
        body = [f'<{tag}>{escape(ctitle)}</{tag}>']
        body += [f'<p>{escape(p)}</p>' for p in paras]
        z.writestr(f'OEBPS/{href}',
                   XHTML.format(t=escape(ctitle), css='../style.css',
                                b='\n'.join(body)))
        items.append((cid, href, ctitle))

    # NAV (epub3 目录)
    lis = '\n'.join(f'<li><a href="{h}">{escape(t)}</a></li>' for _, h, t in items)
    z.writestr('OEBPS/nav.xhtml', XHTML.format(
        t='目录', css='style.css',
        b=f'<nav epub:type="toc"><h2>目录</h2>\n<ol>\n{lis}\n</ol></nav>'))

    # NCX (epub2 目录)
    navs = '\n'.join(
        f'<navPoint id="np{i}" playOrder="{i}"><navLabel><text>{escape(t)}</text></navLabel>'
        f'<content src="{h}"/></navPoint>'
        for i, (_, h, t) in enumerate(items, 1))
    z.writestr('OEBPS/toc.ncx',
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        f'<head><meta name="dtb:uid" content="{book_id}"/>'
        '<meta name="dtb:depth" content="1"/><meta name="dtb:totalPageCount" content="0"/>'
        '<meta name="dtb:maxPageNumber" content="0"/></head>\n'
        f'<docTitle><text>{escape(title)}</text></docTitle>\n'
        f'<navMap>\n{navs}\n</navMap>\n</ncx>\n')

    # OPF 包描述
    man = ['<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
           '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
           '<item id="css" href="style.css" media-type="text/css"/>',
           '<item id="titlepage" href="text/title.xhtml" media-type="application/xhtml+xml"/>']
    man = cover_items + man
    man += [f'<item id="{cid}" href="{h}" media-type="application/xhtml+xml"/>'
            for cid, h, _ in items]
    spine = ['<itemref idref="titlepage"/>']
    if cover_items:
        spine.insert(0, '<itemref idref="coverpage"/>')
    spine += [f'<itemref idref="{cid}"/>' for cid, _, _ in items]
    spine += ['<itemref idref="nav" linear="no"/>']
    z.writestr('OEBPS/content.opf',
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="bookid" xml:lang="' + lang + '">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'<dc:identifier id="bookid">{book_id}</dc:identifier>\n'
        f'<dc:title>{escape(title)}</dc:title>\n'
        f'<dc:creator id="creator">{escape(author)}</dc:creator>\n'
        f'<dc:language>{lang}</dc:language>\n'
        f'<meta property="dcterms:modified">{now}</meta>\n'
        f'        {cover_meta}'
        '</metadata>\n'
        '<manifest>\n' + '\n'.join(man) + '\n</manifest>\n'
        '<spine toc="ncx">\n' + '\n'.join(spine) + '\n</spine>\n'
        '</package>\n')
    z.close()

# ---------------- MOBI / AZW3 ----------------
def find_converter():
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, 'kindlegen.exe')
    if os.path.exists(p):
        return 'kindlegen', p
    for d in (r'D:\APP\Calibre2', r'C:\Program Files\Calibre2'):
        p = os.path.join(d, 'ebook-convert.exe')
        if os.path.exists(p):
            return 'calibre', p
    return None

def make_mobi_azw3(epub_path, out_dir, fmts, converter):
    kind, exe = converter
    base = os.path.splitext(os.path.basename(epub_path))[0]
    results = []
    if kind == 'calibre':
        for fmt in fmts:
            out = os.path.join(out_dir, base + '.' + fmt)
            r = subprocess.run([exe, epub_path, out],
                               capture_output=True, text=True,
                               encoding='utf-8', errors='replace')
            good = r.returncode == 0 and os.path.exists(out)
            results.append((out, good, r.stderr[-500:] if not good else ''))
    else:  # kindlegen：生成 KF7/KF8 混合 .mobi，再切出纯 KF8 的 .azw3
        out = os.path.join(out_dir, base + '.mobi')
        r = subprocess.run([exe, os.path.basename(epub_path),
                            '-o', os.path.basename(out)],
                           capture_output=True, text=True,
                           encoding='utf-8', errors='replace', cwd=out_dir)
        # kindlegen 返回 1 表示有警告但生成成功
        good = r.returncode in (0, 1) and os.path.exists(out)
        results.append((out, good, r.stdout[-500:] if not good else ''))
        if 'azw3' in fmts:
            azw3 = os.path.join(out_dir, base + '.azw3')
            try:
                results.append((azw3, split_kf8(out, azw3), ''))
            except Exception as e:
                results.append((azw3, False, str(e)))
    return results

def split_kf8(hybrid, azw3_out):
    """从 kindlegen 混合文件（KF7+KF8）中切出纯 KF8 部分作为 .azw3。
    KF8 段的图片数据实际存放在 KF7 段（边界之前），需要一并拷贝并修正索引。"""
    data = open(hybrid, 'rb').read()
    n = struct.unpack('>H', data[76:78])[0]
    offs = [struct.unpack('>I', data[78 + 8 * i:82 + 8 * i])[0] for i in range(n)]
    offs.append(len(data))
    # 记录 0 的 EXTH 中类型 121 = KF8 起始记录号
    r0 = data[offs[0]:offs[1]]
    boundary = None
    p = r0.find(b'EXTH')
    if p != -1:
        reclen = struct.unpack('>I', r0[p + 4:p + 8])[0]
        q, end = p + 12, p + reclen
        while q + 8 <= end:
            typ, ln = struct.unpack('>II', r0[q:q + 8])
            if typ == 121:
                boundary = struct.unpack('>I', r0[q + 8:q + 12])[0]
            q += ln
    if not boundary or boundary >= n:
        return False
    cnt = n - boundary

    def _is_image(b):
        return b[:3] == b'\xff\xd8\xff' or b[:8] == b'\x89PNG\r\n\x1a\n'

    # KF8 段自身的图片（kindlegen 混合文件把图片放在 KF7 段，需拷贝）
    kf8_has_image = any(
        _is_image(data[offs[i]:offs[i] + 8]) for i in range(boundary, n))
    extra = []
    if not kf8_has_image:
        extra = [data[offs[i]:offs[i + 1]] for i in range(1, boundary)
                 if _is_image(data[offs[i]:offs[i] + 8])]
    total = cnt + len(extra)

    header = bytearray(data[:76]) + struct.pack('>H', total)
    # 新记录表: 保留原属性/uid 字节，偏移按数据区起点重新计算
    new_base = 78 + 8 * total + 2
    delta = new_base - offs[boundary]
    tbl = bytearray()
    for i in range(cnt):
        src = 78 + 8 * (boundary + i)
        rel = offs[boundary + i] + delta
        tbl += struct.pack('>I', rel) + data[src + 4:src + 8]
    body = bytearray(data[offs[boundary]:])
    if extra:
        pos_rel = len(body)
        for chunk in extra:
            tbl += struct.pack('>I', new_base + pos_rel) + b'\x00\x00\x00\x00'
            pos_rel += len(chunk)
        body += b''.join(extra)

    if extra:
        # 原位修补记录 0: 首图基址(0x6C) 指向新追加的封面记录
        k8_r0 = bytes(body[0:offs[boundary + 1] - offs[boundary]])
        base = cnt                        # 第一张追加图 = 封面
        ep = k8_r0.find(b'EXTH')
        if ep != -1:
            total_exth = struct.unpack('>I', k8_r0[ep + 4:ep + 8])[0]
            q2, e2 = ep + 12, ep + total_exth
            while q2 + 8 <= e2:
                typ, ln = struct.unpack('>II', k8_r0[q2:q2 + 8])
                if typ == 201 and ln >= 12:
                    struct.pack_into('>I', body, q2 + 8, 0)  # 封面 = 基址 + 0
                q2 += ln
            struct.pack_into('>I', body, 108, base)
    open(azw3_out, 'wb').write(bytes(header) + bytes(tbl) + b'\x00\x00' + bytes(body))
    return True

# ---------------- 主流程 ----------------
def convert_file(path, out_dir, fmts, title_opt, author_opt, converter):
    t0 = time.time()
    title, author, chapters = parse_book(path)
    title = title_opt or title
    author = author_opt or author
    print(f'{title}: {len(chapters)} 个章节单元', flush=True)
    ok = True
    ep = os.path.join(out_dir, title + '.epub')
    build_epub((title, author, 'zh'), chapters, ep)
    print(f'  [OK] {os.path.basename(ep)}  ({os.path.getsize(ep)//1024} KB)', flush=True)

    rest = [f for f in fmts if f != 'epub']
    if rest:
        if not converter:
            print('  [跳过] 未找到 Calibre/kindlegen，无法生成 ' + ','.join(rest))
            ok = False
        else:
            for out, good, err in make_mobi_azw3(ep, out_dir, rest, converter):
                size = f'  ({os.path.getsize(out)//1024} KB)' if good else ''
                print(f'  [{"OK" if good else "FAIL"}] {os.path.basename(out)}{size}',
                      flush=True)
                if not good:
                    print('    ' + err, flush=True)
                ok = ok and good
    print(f'{title}: 完成，用时 {time.time()-t0:.0f}s', flush=True)
    return ok

def main():
    ap = argparse.ArgumentParser(description='TXT 小说转 EPUB/MOBI/AZW3')
    ap.add_argument('inputs', nargs='+', help='txt 文件或目录')
    ap.add_argument('-o', '--outdir', default=None, help='输出目录')
    ap.add_argument('-f', '--formats', default='epub',
                    help='epub,mobi,azw3 组合（默认 epub）')
    ap.add_argument('-a', '--author', default=None, help='作者名')
    ap.add_argument('-t', '--title', default=None, help='书名')
    ap.add_argument('--lang', default='zh')
    args = ap.parse_args()

    files = []
    for p in args.inputs:
        if os.path.isdir(p):
            files += [os.path.join(p, g) for g in sorted(os.listdir(p))
                      if g.lower().endswith('.txt')]
        else:
            files.append(p)
    fmts = [f.strip().lower() for f in args.formats.split(',')]
    converter = find_converter()
    print('转换器:', converter[0] if converter else '无（仅支持 epub）', flush=True)

    failed = []
    for f in files:
        od = args.outdir or os.path.dirname(os.path.abspath(f))
        os.makedirs(od, exist_ok=True)
        if not convert_file(f, od, fmts, args.title, args.author, converter):
            failed.append(f)
    if failed:
        print('失败: ' + ', '.join(os.path.basename(x) for x in failed))
        sys.exit(1)
    print('全部完成')

if __name__ == '__main__':
    main()
