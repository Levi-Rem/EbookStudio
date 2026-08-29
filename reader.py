# -*- coding: utf-8 -*-
"""
reader.py — 阅读内核：把各格式统一成「章节列表 + 按章懒加载」

内存约束：任何格式都只把当前章节交给 UI，绝不整本载入渲染。
"""
import os, re, struct, zipfile

# ---------------- PalmDOC (LZ77) 解压 ----------------
def palmdoc_decompress(data):
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        i += 1
        if b == 0:
            out.append(0)
        elif b <= 8:
            out += data[i:i + b]
            i += b
        elif b <= 0x7F:
            out.append(b)
        elif b <= 0xBF:
            if i >= n:
                break
            pair = (b << 8) | data[i]
            i += 1
            dist = (pair >> 3) & 0x7FF
            length = (pair & 7) + 3
            if dist == 0 or dist > len(out):
                # 损坏数据容错：跳过无效回引，避免整本打不开
                out.append(0x20)
                continue
            for _ in range(length):
                out.append(out[-dist])
        else:
            out.append(0x20)
            out.append(b & 0x7F)
    return bytes(out)

def _strip_trailing(data, flags):
    """按 extra_flags 去掉文本记录尾部附加数据（逻辑对齐 calibre 读取器）"""
    def sizeof_trailing_entry(psize):
        bitpos, result = 0, 0
        while True:
            v = data[psize - 1]
            result |= (v & 0x7F) << bitpos
            bitpos += 7
            psize -= 1
            if (v & 0x80) != 0 or bitpos >= 28 or psize == 0:
                return result

    num = 0
    size = len(data)
    f = flags >> 1
    while f:
        if f & 1:
            num += sizeof_trailing_entry(size - num)
        f >>= 1
    if flags & 1 and size - num - 1 >= 0:
        num += (data[size - num - 1] & 0x3) + 1
    return data[:size - num]

def _mobi_text_html(path):
    """解压并拼接 MOBI/AZW3 的文本记录，返回 html 全文"""
    import metadata as _md
    with open(path, 'rb') as f:
        f.seek(60)
        if f.read(4) != b'BOOK':
            raise ValueError('不是 MOBI/AZW3')
        offs = _md.pdb_offsets(f)
        r0 = _read(f, offs, 0)
        if r0[16:20] != b'MOBI':
            raise ValueError('record0 异常')
        compression = struct.unpack('>H', r0[0:2])[0]
        text_count = struct.unpack('>H', r0[8:10])[0]
        # extra_flags 位置: KF7 通常在 0xF2，KF8 在 0xF0；用「记录1解压后应以'<'开头」自校验
        flags_candidates = []
        if len(r0) >= 0xF4:
            flags_candidates.append(struct.unpack('>H', r0[0xF2:0xF4])[0])
        if len(r0) >= 0xF2:
            flags_candidates.append(struct.unpack('>H', r0[0xF0:0xF2])[0])
        flags_candidates += [1, 3, 5, 7, 0]
        flags = 0
        d1 = _read(f, offs, 1)
        for cand in flags_candidates:
            if compression == 2:
                try:
                    test = palmdoc_decompress(_strip_trailing(d1, cand))
                except Exception:
                    continue
            else:
                test = _strip_trailing(d1, cand)
            if test.lstrip()[:1] == b'<':
                flags = cand
                break
        parts = []
        for i in range(1, text_count + 1):
            d = _read(f, offs, i)
            if compression == 2:
                d = palmdoc_decompress(_strip_trailing(d, flags))
            else:
                d = _strip_trailing(d, flags)
            parts.append(d)
        return b''.join(parts).decode('utf-8', 'replace')

def _read(f, offs, i):
    f.seek(offs[i])
    return f.read(offs[i + 1] - offs[i])

# ---------------- 章节切分 ----------------
TAG_RE = re.compile(r'<[^>]+>')
PAGEBREAK_RE = re.compile(r'(?i)<mbp:pagebreak[^>]*>')
HEAD_RE = re.compile(r'(?is)<h[1-6][^>]*>(.*?)</h[1-6]>')
# guide 中的目录页引用（kindlegen / calibre 产出的文件都有）
GUIDE_TOC_RE = re.compile(
    r'(?is)<reference[^>]*type="toc"[^>]*filepos="(\d+)"'
    r'|<reference[^>]*filepos="(\d+)"[^>]*type="toc"')
TOC_LINK_RE = re.compile(r'(?is)<a[^>]+filepos="(\d+)"[^>]*>(.*?)</a>')
CHTITLE_RE = re.compile(
    r'第\s*[0-9０-９零一二三四五六七八九十百千万两]{1,10}\s*[章卷部集回][^\s<>。，]{0,25}')

def _clean_title(raw, limit=60):
    t = TAG_RE.sub('', raw)
    t = re.sub(r'\s+', ' ', t).strip()
    return t[:limit]

def extract_toc_positions(html):
    """从内嵌目录页提取 [(字符偏移, 标题)]。失败返回 []"""
    m = GUIDE_TOC_RE.search(html)
    if not m:
        return []
    toc_pos = int(m.group(1) or m.group(2))
    window = html[toc_pos:toc_pos + 65536]
    end = PAGEBREAK_RE.search(window)
    if end and end.start() > 64:
        window = window[:end.start()]
    entries = []
    for pos, title in TOC_LINK_RE.findall(window):
        title = _clean_title(title, 80)
        if not title:
            continue
        entries.append((int(pos), title))
    if len(entries) < 5:
        return []
    # 偏移必须单调不减且在全文范围内；去掉指向开头的"目录"自身链接
    out, last = [], -1
    for pos, title in entries:
        if pos < last:
            return []
        last = pos
        out.append((pos, title))
    if len(out) > 2 and out[0][0] == 0:
        out = out[1:]
    return out

def _title_of(chunk):
    m = HEAD_RE.search(chunk)
    if m:
        t = TAG_RE.sub('', m.group(1)).strip()
        if t:
            return t[:60]
    text = TAG_RE.sub('', chunk[:500]).strip()
    return (text[:24] + '…') if text else '（无标题）'

def split_html_chapters(html, min_chunks=8):
    """把整本 html 切成章节 [(标题, 起, 止)]（正文按偏移切片，不复制）。
    优先级: 内嵌目录页精确切分 > 分页符 > 标题推断 > 定长分块"""
    total = len(html)
    # 1) 目录页锚点：标题与偏移都是文件自带的，最精确
    toc = extract_toc_positions(html)
    if len(toc) >= 5:
        bounds = sorted({p for p, _ in toc if 0 <= p < total})
        if bounds and bounds[0] > 64:          # 目录前的前言部分
            pre_end = bounds[0]
            chunks = [(_title_of(html[:pre_end]) or '前言', 0, pre_end)]
        else:
            chunks = []
        for i, pos in enumerate(bounds):
            end = bounds[i + 1] if i + 1 < len(bounds) else total
            if not html[pos:end].strip():
                continue
            title = next((t for p, t in toc if p == pos), None) \
                or _title_of(html[pos:end])
            chunks.append((title, pos, end))
        if len(chunks) >= 5:
            return chunks

    # 2) 分页符（段 = 前一分隔符尾 → 下一分隔符头，不含标签本身）
    ms = list(PAGEBREAK_RE.finditer(html))
    if len(ms) + 1 >= min_chunks:
        parts, prev_end = [], 0
        for m in ms:
            if html[prev_end:m.start()].strip():
                parts.append((prev_end, m.start()))
            prev_end = m.end()
        if html[prev_end:total].strip():
            parts.append((prev_end, total))
        if len(parts) >= min_chunks:
            return [(_title_of(html[s:e]), s, e) for s, e in parts]

    # 3) 标题推断（仅接受像章节名的标题）
    pos = [(m.start(), _clean_title(m.group(1)))
           for m in HEAD_RE.finditer(html)]
    pos = [(p, t) for p, t in pos if t and (
        CHTITLE_RE.search(t) or len(t) <= 30)]
    if len(pos) >= min_chunks:
        edges = [p for p, _ in pos] + [total]
        ans = [(_title_of(html[edges[i]:edges[i + 1]]), edges[i], edges[i + 1])
               for i in range(len(pos)) if html[edges[i]:edges[i + 1]].strip()]
        if len(ans) >= min_chunks:
            return ans

    # 4) 定长分块兜底
    size = max(65536, total // 120)
    return [(_title_of(html[s:s + size]), s, min(s + size, total))
            for s in range(0, total, size) if html[s:s + size].strip()]

# ---------------- 统一入口：BookReader ----------------
class BookReader:
    """统一阅读接口。chapters: [(标题,)]; get_chapter(i) -> (标题, html)"""

    def __init__(self):
        self.z = None            # EPUB zip 句柄
        self.cur = 0
        self.chapters = []

    def open(self, path):
        self.path = path
        ext = os.path.splitext(path)[1].lower()
        if ext == '.txt':
            return self._open_txt(path)
        if ext == '.epub':
            return self._open_epub(path)
        if ext in ('.mobi', '.azw3'):
            return self._open_mobi(path)
        raise ValueError(f'不支持的格式: {ext}')

    def close(self):
        """释放持有的文件句柄（EPUB zip）"""
        if self.z is not None:
            try:
                self.z.close()
            except Exception:
                pass
            self.z = None

    # TXT：复用转换内核的章节解析（已验证）
    def _open_txt(self, path):
        import txt2ebook
        _, _, chapters = txt2ebook.parse_book(path)
        self.chapters = [(t,) for _, t, _ in chapters]
        self._txt_chapters = chapters
        self._kind = 'txt'
        return self

    def _chapter_html(self, level, title, paras):
        tag = 'h1' if level == 1 else 'h2'
        body = [f'<{tag}>{title}</{tag}>']
        body += [f'<p>{p}</p>' for p in paras]
        return '\n'.join(body)

    # EPUB：spine 逐文件懒加载
    ITEM_RE = re.compile(r'<item\s[^>]*/?>')
    ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')

    def _open_epub(self, path):
        self.z = zipfile.ZipFile(path)
        opf_name = None
        mm = re.search(rb'full-path="([^"]+)"', self.z.read('META-INF/container.xml'))
        opf_name = mm.group(1).decode() if mm else None
        if not opf_name:
            for n in self.z.namelist():
                if n.endswith('.opf'):
                    opf_name = n
                    break
        if not opf_name:
            self.z.close()
            raise ValueError('EPUB 缺少 OPF')
        opf = self.z.read(opf_name).decode('utf-8', 'replace')
        base = os.path.dirname(opf_name)
        # 属性顺序无关地解析 manifest: id -> href
        manifest = {}
        for tag in self.ITEM_RE.findall(opf):
            attrs = dict(self.ATTR_RE.findall(tag))
            iid, href = attrs.get('id'), attrs.get('href')
            if iid and href:
                manifest[iid] = href
        spine = re.findall(r'<itemref[^>]+idref="([^"]+)"', opf)
        self.spine = []
        for idref in spine:
            href = manifest.get(idref)
            if href:
                full = re.sub(r'[^/]+/\.\./', '', (base + '/' + href).lstrip('/'))
                self.spine.append(full)
        if not self.spine:
            self.z.close()
            raise ValueError('EPUB spine 为空')
        self._kind = 'epub'
        self.cur = 0
        # 目录先用序号，打开时补标题
        self.chapters = [(f'第 {i+1} 节',) for i in range(len(self.spine))]
        return self

    # MOBI/AZW3：全文只解压一次；章节存 (标题, 起, 止) 偏移，取章时才切片
    def _open_mobi(self, path):
        self._html = _mobi_text_html(path)
        self._bounds = split_html_chapters(self._html)
        self.chapters = [(t,) for t, _, _ in self._bounds]
        self._kind = 'mobi'
        return self

    # ---- 取当前章内容 ----
    def get_chapter(self, idx):
        if self._kind == 'txt':
            level, title, paras = self._txt_chapters[idx]
            return title, self._chapter_html(level, title, paras)
        if self._kind == 'epub':
            href = self.spine[idx]
            html = self.z.read(href).decode('utf-8', 'replace')
            t = _title_of(html)
            if not t.strip('<> '):
                t = f'第 {idx+1} 节'
            self.chapters[idx] = (t[:60],)
            return t, html
        title, start, end = self._bounds[idx]
        return title, self._html[start:end]

    # ---- EPUB 相对资源（图片）解析给 QTextBrowser ----
    def epub_resource(self, url_path):
        if self._kind != 'epub':
            return None
        cur_dir = os.path.dirname(self.spine[min(self.cur, len(self.spine) - 1)])
        full = re.sub(r'[^/]+/\.\./', '', (cur_dir + '/' + url_path).lstrip('/'))
        for cand in {full, url_path.lstrip('/')}:
            try:
                return self.z.read(cand)
            except (KeyError, OSError):
                continue
        return None

    # ---- 全文搜索 ----
    def chapter_text(self, idx):
        _, body = self.get_chapter(idx)
        text = TAG_RE.sub(' ', body)
        import html as html_mod
        return html_mod.unescape(text)

    def search(self, query, cap=300, per_chapter=3):
        """全文搜索，返回 [(章节序号, 章节标题, 片段)]，大小写不敏感"""
        q = query.strip()
        if not q:
            return []
        ql = q.lower()
        hits = []
        for idx in range(len(self.chapters)):
            try:
                text = self.chapter_text(idx)
            except Exception:
                continue
            tl = text.lower()
            count, start = 0, 0
            while count < per_chapter:
                p = tl.find(ql, start)
                if p < 0:
                    break
                s = max(0, p - 30)
                snippet = re.sub(r'\s+', ' ', text[s:p + len(q) + 40]).strip()
                hits.append((idx, self.chapters[idx][0], snippet))
                count += 1
                start = p + len(q)
                if len(hits) >= cap:
                    return hits
        return hits
