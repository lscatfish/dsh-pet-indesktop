# -*- coding: utf-8 -*-
"""打包产物 Qt 死重清理（缩小安装包体积）。

桌面宠物是纯 QWidget 应用（QtCore/QtGui/QtWidgets/QtMultimedia），完全不使用
QML/Quick。但 PyInstaller 的 PySide6.QtMultimedia hook 会把整套 Qt6Qml/Qt6Quick
全家桶连同 Mesa 软渲染器 opengl32sw.dll 一并收集进产物（Windows webm 版实测
约 38MB），此外 Qt6Pdf/Qt6VirtualKeyboard 这两个孤儿库（包内没有任何 .pyd
引用它们）也纯属浪费。本脚本在 PyInstaller 构建完成后删掉这些确定无用的文件。

跨平台布局（PyInstaller onedir）：
- Windows/Linux：<bundle>/_internal/PySide6/...（Qt 动态库 Windows 在 PySide6/
  顶层，Linux 在 PySide6/Qt/ 下，macOS 在 PySide6/Qt/ 下），因此做递归扫描；
- macOS .app：<bundle>/Contents/Frameworks/PySide6/...；
- 另兜底扫描 lib 根目录顶层，覆盖 Linux 下个别 Qt 库被拍平到 _internal/ 的情况。

安全性依据：
- 包内保留的 Qt6* 库 / Python 扩展对目标前缀零依赖（曾用 objdump 逐文件核对）；
- 例外：Qt 6.8+ 的 ffmpeg 多媒体后端插件（plugins/multimedia/ 下）动态链接
  Qt6Qml/Qt6QmlMeta/Qt6QmlModels/Qt6QmlWorkerScript/Qt6Quick 这 5 个库，
  由 _FFMPEG_BACKEND_KEEP 精确豁免（早期核对未覆盖插件依赖，Linux 上曾因此
  导致 QMediaPlayer/QAudioDecoder 全面不可用，压缩音效静默失声）。

用法（onedir 目录或 macOS .app 均自动识别）：
    python scripts/trim_bundle_qt.py --dir dist-onedir/dsh-pet-standalone-webm
    python scripts/trim_bundle_qt.py --dir build/macos/dsh-pet-standalone-webm.app

退出码：0 = 正常（含无目标可删，仅告警）；1 = 未找到 PySide6 目录（构建脚本应中止）。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# 待删除的 Qt 模块基名（匹配时忽略平台差异：Windows .dll / macOS 无扩展名 /
# Linux lib*.so[.6] / Python .pyd）。全部为 QML/Quick 家族或确定无引用的孤儿：
# - Qt6Qml / Qt6Quick 前缀家族：PySide6.QtMultimedia hook 连带收集，应用零引用
# - Qt6Pdf / Qt6VirtualKeyboard：包内没有对应 .pyd 的孤儿库
# - opengl32sw：Mesa 软渲染器，仅创建 QOpenGLContext 时才可能加载（本应用不会）
# 前缀匹配是安全的：这些前缀不会命中任何被使用的 Qt 模块。
TARGET_PREFIXES = (
    "Qt6Qml",
    "Qt6Quick",
    "Qt6Pdf",
    "Qt6VirtualKeyboard",
    "QtQml",
    "QtQuick",
    "QtPdf",
    "QtVirtualKeyboard",
    "opengl32sw",
)

# 豁免：Qt 6.8+ 的 ffmpeg 多媒体后端插件（plugins/multimedia/
# libffmpegmediaplugin.so / ffmpegmediaplugin.dll）动态链接以下 5 个库
# （QRhi 视频渲染路径所需；Linux Qt 6.11.2 实测 ldd 依赖闭包）。
# 删掉它们会导致插件 dlopen 失败（"No QtMultimedia backends found"），
# QMediaPlayer / QAudioDecoder 全部不可用，压缩音效（mp3 等）静默失声——
# WAV 音效走 QSoundEffect 不依赖后端插件，故障极具隐蔽性。
# 注意按归一化基名精确匹配：同名 Python 扩展（QtQml.abi3.so / QtQml.pyd，
# 基名无 "6"）不在豁免之列，仍会被删除。
_FFMPEG_BACKEND_KEEP = frozenset({
    "Qt6Qml",
    "Qt6QmlMeta",
    "Qt6QmlModels",
    "Qt6QmlWorkerScript",
    "Qt6Quick",
})

# 需要整体删除的目录（按 PySide6 包内相对位置），QML 资源与 QML 调试插件
_QML_DIRS = (
    "qml",
    "Qt/qml",
    "plugins/qmltooling",
    "Qt/plugins/qmltooling",
)


def find_layout(bundle_root: Path) -> tuple[Path, Path] | None:
    """定位 PySide6 包目录与其所在的 lib 根目录（_internal / Contents/Frameworks）。"""
    candidates = (
        (bundle_root / "PySide6", bundle_root),
        (bundle_root / "_internal" / "PySide6", bundle_root / "_internal"),
        (bundle_root / "Contents" / "Frameworks" / "PySide6", bundle_root / "Contents" / "Frameworks"),
    )
    for pyside6_dir, lib_root in candidates:
        if pyside6_dir.is_dir():
            return pyside6_dir, lib_root
    return None


def _is_target(name: str) -> bool:
    """判断文件名是否属于待删 Qt 模块（处理 lib 前缀与版本扩展名）。"""
    base = name[3:] if name.startswith("lib") else name
    # stem = 第一个 '.' 之前的部分：Qt6Quick.dll / libQt6Quick.so.6 / Qt6Quick 均归一
    stem = base.split(".", 1)[0]
    if stem in _FFMPEG_BACKEND_KEEP:
        return False
    return stem.startswith(TARGET_PREFIXES)


def _delete_files(root: Path, recursive: bool) -> int:
    """删除 root 下的目标文件，返回释放字节数（recursive=False 仅扫顶层文件）。"""
    freed = 0
    iterator = root.rglob("*") if recursive else root.iterdir()
    for path in sorted(iterator):
        if path.is_file() and _is_target(path.name):
            size = path.stat().st_size
            path.unlink()
            print(f"  - {path.relative_to(root)} ({size / 1024 / 1024:.1f} MB)")
            freed += size
    return freed


def trim_pyside6(pyside6_dir: Path, lib_root: Path) -> int:
    """删除 PySide6 包内的死重 Qt 库与 QML 资源目录，返回释放的字节数。"""
    freed = 0
    # 递归扫 PySide6 包：Windows 顶层 .dll / macOS·Linux 的 PySide6/Qt/ 子树
    freed += _delete_files(pyside6_dir, recursive=True)
    # 兜底：lib 根目录（_internal / Contents/Frameworks）顶层，覆盖 Linux 拍平布局
    if lib_root != pyside6_dir:
        freed += _delete_files(lib_root, recursive=False)
    # QML 资源与调试插件目录（按 PySide6 包内相对位置，兼容 Qt/ 子目录布局）
    for rel in _QML_DIRS:
        target = pyside6_dir / rel
        if target.is_dir():
            size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
            shutil.rmtree(target)
            print(f"  - {target.relative_to(pyside6_dir)}/ ({size / 1024 / 1024:.1f} MB)")
            freed += size
    return freed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", required=True, help="PyInstaller onedir 目录或 macOS .app 路径")
    args = parser.parse_args(argv)

    bundle_root = Path(args.dir).resolve()
    if not bundle_root.is_dir():
        print(f"错误：目录不存在：{bundle_root}", file=sys.stderr)
        return 1

    layout = find_layout(bundle_root)
    if layout is None:
        print(f"错误：未找到 PySide6 目录（{bundle_root}）", file=sys.stderr)
        return 1
    pyside6_dir, lib_root = layout

    print(f"清理 Qt 死重：{pyside6_dir}")
    freed = trim_pyside6(pyside6_dir, lib_root)
    if freed == 0:
        # 不同平台/版本的 hook 收集内容可能不同；无目标可删时告警但不算失败
        print("提示：未找到待清理的 Qt 模块（可能该版本/平台已不含死重）")
    else:
        print(f"完成，共释放 {freed / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
