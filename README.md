# EbookStudio

迷你 Calibre：书库管理 + 高速格式转换 + 内置阅读器，纯 Python 实现。

针对 Calibre 的两大痛点设计：**打开大 mobi 内存暴涨** 与 **转换大书极慢**。

## 特性

### 书库管理
- 扫描目录 / 添加文件导入（txt / epub / mobi / azw3）
- SQLite 存储、按路径去重、书名/作者即时搜索、列排序
- 元数据只读文件头几 KB（mobi 解析 PDB/EXTH、epub 解析 container/OPF），绝不全量加载
- 封面自动提取（EXTH 201 / OPF cover-image），无封面书自动生成文字封面

### 格式转换（快）
- **TXT → EPUB / MOBI / AZW3**
- EPUB 由纯 Python 标准库生成（章节拆分、NCX+NAV 双目录、卷/章分级、分页）；实测 12MB / 1650 章的 txt 约 1 秒生成 EPUB
- MOBI/AZW3 由 Amazon 官方 kindlegen 编译器从 EPUB 编译，自动生成文字封面嵌入全部格式
- 混合 mobi 切分为 azw3 时自动拷贝图片记录并修正 EXTH 索引
- 转换在独立子进程执行，内存随进程释放，UI 永不卡死
- 中文优化：GBK/GB18030/UTF-8 自动识别、`第X章/卷/部/集/回` + 楔子/序章/后记等章节识别、书名作者从文件名提取

### 内置阅读器
- 四格式统一「章节目录 + 按章懒加载」，任何时刻只渲染当前章
- 自研 PalmDOC(LZ77) 解压器 + MOBI 记录尾部附加数据剥离 + extra_flags 自校验
- 章节切分四级策略：内嵌目录页锚点 > 分页符 > 标题推断 > 定长分块
- 全文搜索（Ctrl+F）：结果列表跳转 + 本章全部命中高亮 + 定位到所点条目
- 书签（Ctrl+B）：添加/双击跳转（精确到章内滚动位置）/删除
- 按键方案：`PageUp/PageDown` 翻页（章末自动进下一章）、`←/→` 切章、`↑/↓` 滚行
- 字号、夜间模式记忆（QSettings）；阅读进度记忆（重开自动回到上次位置）
- 阅读历史面板：双击任意记录打开对应书并恢复位置

## 运行

```bash
pip install PySide6
python app.py
```

Python 3.10+，Windows / Linux / macOS（kindlegen.exe 为 Windows 版，其他平台请自行放置对应版本到项目根目录）。

## 项目结构

| 文件 | 职责 |
|---|---|
| `app.py` | 主窗口：书库表格、详情面板、扫描导入、转换调度（QProcess）、阅读历史 |
| `reader_window.py` | 阅读器窗口：章节目录、搜索高亮、书签、按键翻页、进度恢复 |
| `reader.py` | 阅读内核：PalmDOC 解压、mobi 文本提取、四级章节切分、EPUB spine 懒加载、全文搜索 |
| `txt2ebook.py` | 转换内核：TXT 章节解析、EPUB 生成器、kindlegen 调度、hybrid 切 azw3 |
| `convert_worker.py` | 转换子进程：封面绘制（离屏 Qt）、批量转换、进度上报 |
| `metadata.py` | 轻量元数据探测：PDB/MOBI/EXTH、EPUB OPF、封面提取 |
| `library.py` | SQLite 书库：书目、书签、阅读进度、阅读历史（含旧库迁移） |
| `smoke_test.py` | 36 项回归测试（逻辑层 + GUI 离屏） |
| `kindlegen.exe` | Amazon 官方编译器（Windows 版，转换 mobi/azw3 所需） |

## 测试

```bash
python smoke_test.py
```

覆盖：元数据探测、书库增删查、四格式章节切分、全文搜索、书签/进度/历史、
转换 worker（三格式 + 封面嵌入）、按键翻页、级联清理、损坏数据容错。

## License

MIT
