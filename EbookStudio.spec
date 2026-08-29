# -*- mode: python ; coding: utf-8 -*-
# EbookStudio PyInstaller 打包配置:  pyinstaller EbookStudio.spec
import os

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('kindlegen.exe', '.')],      # 编译器随包分发到 exe 目录
    hiddenimports=['convert_worker', 'txt2ebook', 'reader', 'metadata', 'library'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'numpy', 'pandas', 'scipy', 'PIL',
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtCharts',
        'PySide6.QtMultimedia', 'PySide6.QtPdf', 'PySide6.QtSql',
        'PySide6.QtTest', 'PySide6.QtDesigner', 'PySide6.Qt3DCore',
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='EbookStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,               # GUI 程序无控制台
    icon='icon.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='EbookStudio',
)
