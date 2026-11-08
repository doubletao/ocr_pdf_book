# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.hooks import copy_metadata

datas = []
binaries = []
hiddenimports = []
datas += copy_metadata('paddlex')
datas += copy_metadata('paddleocr')
datas += copy_metadata('paddlepaddle')
datas += copy_metadata('paddlepaddle-gpu')
datas += copy_metadata('aistudio-sdk')
datas += copy_metadata('chardet')
datas += copy_metadata('colorlog')
datas += copy_metadata('filelock')
datas += copy_metadata('huggingface-hub')
datas += copy_metadata('modelscope')
datas += copy_metadata('numpy')
datas += copy_metadata('packaging')
datas += copy_metadata('pandas')
datas += copy_metadata('pillow')
datas += copy_metadata('prettytable')
datas += copy_metadata('py-cpuinfo')
datas += copy_metadata('pydantic')
datas += copy_metadata('PyYAML')
datas += copy_metadata('requests')
datas += copy_metadata('ruamel.yaml')
datas += copy_metadata('typing-extensions')
datas += copy_metadata('ujson')
datas += copy_metadata('beautifulsoup4')
datas += copy_metadata('einops')
datas += copy_metadata('ftfy')
datas += copy_metadata('imagesize')
datas += copy_metadata('Jinja2')
datas += copy_metadata('latex2mathml')
datas += copy_metadata('lxml')
datas += copy_metadata('opencv-contrib-python')
datas += copy_metadata('openpyxl')
datas += copy_metadata('premailer')
datas += copy_metadata('pyclipper')
datas += copy_metadata('pypdfium2')
datas += copy_metadata('python-bidi')
datas += copy_metadata('regex')
datas += copy_metadata('safetensors')
datas += copy_metadata('scikit-learn')
datas += copy_metadata('scipy')
datas += copy_metadata('sentencepiece')
datas += copy_metadata('shapely')
datas += copy_metadata('tiktoken')
datas += copy_metadata('tokenizers')
tmp_ret = collect_all('paddle')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('paddlex')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('paddleocr')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('safetensors')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pypdfium2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('nvidia')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['pack_entry.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='BookScan2EPUB',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='BookScan2EPUB',
)
