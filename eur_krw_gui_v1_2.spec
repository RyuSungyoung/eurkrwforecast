# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# Collect Playwright driver, node.exe, and modules
pw_datas, pw_binaries, pw_hidden = collect_all('playwright')
datas += pw_datas
binaries += pw_binaries
hiddenimports += pw_hidden

# Exclude unnecessary heavy packages from python environment
excludes = [
    'torch',
    'torchvision',
    'torchaudio',
    'scipy',
    'transformers',
    'tokenizers',
    'safetensors',
    'huggingface_hub',
    'sentencepiece',
    'scikit_learn',
    'sklearn',
    'notebook',
    'jupyter',
    'jupyterlab',
    'IPython',
    'ipywidgets',
    'tornado',
    'pygments',
    'sympy',
    'debugpy',
    'pytest',
    'unittest',
    'pydantic',
    'google',
    'grpc',
    'matplotlib.tests',
    'numpy.tests',
    'pandas.tests',
]

a = Analysis(
    ['D:\\VSC\\eur_krw_gui_v2_7.py'],
    pathex=['D:\\VSC'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='일간 유로화 환율 분석기 v1.2',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='D:\\VSC\\version_v12.txt',
)
