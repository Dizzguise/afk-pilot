# Third-party notices

The portable executable bundles the Python runtime, Tcl/Tk, and the PyInstaller
bootloader. Their license texts are included in the release ZIP's `licenses/`
directory, collected from the build environment. For Python installations that omit a
Tcl/Tk notice, the repository includes the upstream 8.6.12 license text from
`tcltk/tcl` and `tcltk/tk` tag `core-8-6-12` as a fallback. Python's license text
includes notices for components of its standard library.

- Python: https://docs.python.org/3/license.html
- Tcl/Tk: https://www.tcl-lang.org/software/tcltk/license.html
- PyInstaller: https://pyinstaller.org/en/stable/license.html

PyInstaller's bootloader distribution exception is included in its license file.
Build-only dependencies are pinned in `requirements-build.txt`.

AFK Pilot's original source code is licensed under the MIT License in `LICENSE`.
Third-party components retain their own licenses and notices listed above.
