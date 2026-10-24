# -*- mode: python ; coding: utf-8 -*-
# Build with:  pyinstaller --noconfirm tv_app.spec
# Output:      dist\TV Remote\TV Remote.exe  (share the whole "TV Remote" folder, zipped)
import os

import accessible_output2

# accessible_output2 looks for its screen reader DLLs in accessible_output2\lib next to the exe.
speech_libs = [(os.path.join(os.path.dirname(accessible_output2.__file__), "lib", "*.dll"),
                os.path.join("accessible_output2", "lib"))]

a = Analysis(
    ["tv_app.pyw"],
    pathex=[],
    binaries=[],
    datas=speech_libs + [("README.md", "."), ("LICENSE", ".")],
    hiddenimports=[
        "accessible_output2.outputs.auto", "accessible_output2.outputs.jaws",
        "accessible_output2.outputs.nvda", "accessible_output2.outputs.sapi5",
        "win32com.client", "win32gui", "pywintypes", "pythoncom",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PIL", "IPython", "numpy", "scipy"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TV Remote",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                  # UPX-packed DLLs often trip antivirus scanners
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory=".",     # keep the libraries beside the exe, where accessible_output2 expects them
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TV Remote",
)
