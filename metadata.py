# -*- coding: utf-8 -*-
"""
metadata.py — 轻量元数据探测

设计约束：任何格式都只读取文件头部（几 KB 内）提取书名/作者/封面，
绝不整本解析 —— 这是规避 Calibre 打开 mobi 内存泄漏问题的核心设计。
"""
import os, re, struct, zipfile

TXT_EXTS = {'.txt', '.epub', '.mobi', '.azw3'}

def probe(path):
    """返回 {title, author, format, size, cover(bytes|None)}，失败字段留空不抛异常"""
    ext = os.path.splitext(path)[1].lower()
    base = os.path.splitext(os.path.basename(path))[0]
    m = re.match(r'《(.+?)》', base)
    title = m.group(1) if m else base
    am = re.search(r'作者[:：\-]\s*(\S+)', base)
    author = am.group(1) if am else ''
    info = {'title': title, 'author': author, 'format': ext.lstrip('.').upper(),
            'size': os.path.getsize(path), 'cover': None}
    try:
        if ext == '.txt':
            pass
        elif ext == '.epub':
            _probe_epub(path, info)
        elif ext in ('.mobi', '.azw3'):
            _probe_mobi(path, info)
    except Exception:
        pass
    if not info['title']:
        info['title'] = title
    return info

# ---------------- EPUB：zip 内只读 container + OPF + 封面 ----------------
def _probe_epub(path, info):
    with zipfile.ZipFile(path) as z:
        opf_name = None
        with z.open('META-INF/container.xml') as f:
            mm = re.search(rb'full-path="([^"]+)"', f.read(4096))
            if mm:
                opf_name = mm.group(1).decode()
        if not opf_name:
            for n in z.namelist():
                if n.endswith('.opf'):
                    opf_name = n
                    break
        if not opf_name:
            return
        opf = z.read(opf_name).decode('utf-8', 'replace')
        t = re.search(r'<dc:title[^>]*>([^<]+)</dc:title>', opf)
        a = re.search(r'<dc:creator[^>]*>([^<]+)</dc:creator>', opf)
        if t:
            info['title'] = t.group(1).strip()
        if a:
            info['author'] = a.group(1).strip()
        # 封面: manifest 中 properties="cover-image"，或 meta name="cover"
        cover_id = None
        mc = re.search(r'<item[^>]+properties="[^"]*cover-image[^"]*"[^>]+id="([^"]+)"', opf) \
             or re.search(r'<item[^>]+id="([^"]+)"[^>]+properties="[^"]*cover-image', opf) \
             or re.search(r'<meta[^>]+name="cover"[^>]+content="([^"]+)"', opf)
        if mc:
            cover_id = mc.group(1)
        if cover_id:
            mi = re.search(rf'<item[^>]+id="{re.escape(cover_id)}"[^>]+href="([^"]+)"', opf) \
                 or re.search(rf'<item[^>]+href="([^"]+)"[^>]+id="{re.escape(cover_id)}"', opf)
            if mi:
                href = mi.group(1)
                full = re.sub(r'[^/]+/\.\./', '', (os.path.dirname(opf_name) + '/' + href).lstrip('/'))
                for cand in {full, href}:
                    try:
                        info['cover'] = z.read(cand)
                        break
                    except KeyError:
                        continue

# ---------------- MOBI/AZW3：PDB + record0 + EXTH，只读头部 ----------------
def pdb_offsets(f):
    """解析 PDB 记录偏移表，兼容两种布局：
    标准 PDB 在 78 偏移有 2 字节间隙（条目从 80 开始）；
    kindlegen/calibre 生成的文件条目直接从 78 开始、间隙在尾部。"""
    f.seek(76)
    num = struct.unpack('>H', f.read(2))[0]
    f.seek(78)
    blob = f.read(8 * num + 2)
    size = os.fstat(f.fileno()).st_size
    for start in (2, 0):
        offs = [struct.unpack('>I', blob[start + 8 * i:start + 8 * i + 4])[0]
                for i in range(num)]
        if num and all(offs[i] < offs[i + 1] for i in range(num - 1)) \
                and 0 < offs[0] < size:
            return offs + [size]
    raise ValueError('PDB 记录表解析失败')

def _read_record(f, offs, i):
    f.seek(offs[i])
    return f.read(offs[i + 1] - offs[i])

def _probe_mobi(path, info):
    with open(path, 'rb') as f:
        f.seek(60)
        if f.read(4) != b'BOOK':
            return
        offs = pdb_offsets(f)
        r0 = _read_record(f, offs, 0)
        if r0[16:20] != b'MOBI':
            return
        header_len = struct.unpack('>I', r0[20:24])[0]
        name_off, name_len = struct.unpack('>II', r0[68:76])
        title = r0[name_off:name_off + name_len].decode('utf-8', 'replace').strip('\x00')
        if title:
            info['title'] = title
        # EXTH
        p = 16 + header_len
        if p + 12 <= len(r0) and r0[p:p + 4] == b'EXTH':
            total = struct.unpack('>I', r0[p + 4:p + 8])[0]
            q, end = p + 12, p + total
            cover_rec = None
            while q + 8 <= end:
                typ, ln = struct.unpack('>II', r0[q:q + 8])
                data = r0[q + 8:q + ln]
                if typ == 100:
                    info['author'] = data.decode('utf-8', 'replace').strip()
                elif typ == 503:
                    t = data.decode('utf-8', 'replace').strip()
                    if t:
                        info['title'] = t
                elif typ == 201:
                    cover_rec = struct.unpack('>I', data)[0]
                q += ln
            # 封面 = 图片基址 + EXTH201 偏移；基址在不同产地的文件里位于
            # 0x6C 或 0x5C，逐一尝试并用图片魔法数校验，失败再全文件扫描
            def _is_image(b):
                return b[:3] == b'\xff\xd8\xff' or b[:8] == b'\x89PNG\r\n\x1a\n'
            bases = []
            if len(r0) >= 112:
                bases.append(struct.unpack('>I', r0[108:112])[0])
            if len(r0) >= 96:
                bases.append(struct.unpack('>I', r0[92:96])[0])
            if cover_rec is not None:
                for base in bases:
                    idx = base + cover_rec
                    if 0 < idx < len(offs) - 1:
                        img = _read_record(f, offs, idx)
                        if _is_image(img):
                            info['cover'] = img
                            break
            if info['cover'] is None:
                # 兜底：逐记录只读头部 8 字节找图片（避免整条读出大记录）
                for i in range(1, len(offs) - 1):
                    f.seek(offs[i])
                    head = f.read(8)
                    if head[:3] == b'\xff\xd8\xff' or \
                            head[:8] == b'\x89PNG\r\n\x1a\n':
                        f.seek(offs[i])
                        info['cover'] = f.read(offs[i + 1] - offs[i])
                        break
