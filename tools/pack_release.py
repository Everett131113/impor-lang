"""打 Impor 发行件：release ZIP + VSIX（不依赖 vsce）。

用法：python tools/pack_release.py            # 默认以仓库根为源
      IMPOR_ROOT=<别的树> python tools/pack_release.py
输出到桌面「关于Impor」目录，同时写出 .sha256。

4.7.2 起口径统一（此前本脚本默认打「安装目录」，与手工打包用的仓库根口径分裂，
两者排除规则不同导致条目数对不上）：源目录 = 仓库根，排除 __pycache__/.git/
dist-cross/.impor-cache，以及工作树根下构建测试残留的 demo_*.exe；压缩级别 9（此前为 6）。
"""
import hashlib
import json
import os
import zipfile

VER = "5.6.0"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.environ.get("IMPOR_ROOT", REPO_ROOT)
OUT = os.environ.get("IMPOR_OUT", r"C:\Users\41482\Desktop\总Impor文件\关于Impor")

SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", ".vs", ".idea", "node_modules",
             "dist-cross", ".impor-cache", ".impor_recovery"}
SKIP_EXT = {".pyc", ".pyo"}
# 工作树根的构建测试残留（不属于发布内容）
SKIP_ROOT_FILES = {"demo_03_strings.exe", "demo_15_array_stats.exe"}
# 4.8.0：性能基准集的原生产物缓存（bench_gate 会重建，不进发布包）
SKIP_REL_PREFIX = ("bench/suite/bin",)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if os.path.splitext(name)[1].lower() in SKIP_EXT:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if rel.replace(os.sep, "/") in SKIP_ROOT_FILES:
                continue
            if rel.replace(os.sep, "/").startswith(SKIP_REL_PREFIX):
                continue
            yield full, rel


def build_zip(out_path, files, prefix=""):
    written = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for full, rel in files:
            arc = prefix + rel.replace(os.sep, "/")
            try:
                zf.write(full, arc)
                written += 1
            except (PermissionError, OSError):
                pass
    return written


CONTENT_TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="json" ContentType="application/json"/>
<Default Extension="vsixmanifest" ContentType="text/xml"/>
<Default Extension="md" ContentType="text/markdown"/>
<Default Extension="txt" ContentType="text/plain"/>
<Default Extension="png" ContentType="image/png"/>
<Default Extension="js" ContentType="application/javascript"/>
<Default Extension="impor" ContentType="text/plain"/>
<Default Extension="tmLanguage" ContentType="text/xml"/>
<Default Extension="yml" ContentType="text/yaml"/>
</Types>
"""

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Language="en-US" Id="vscode-impor" Version="{ver}" Publisher="impor-lang"/>
    <DisplayName>Impor (I++) Language</DisplayName>
    <Description xml:space="preserve">Impor (I++) 语言支持：语法高亮 + LSP</Description>
    <Tags>impor,i++,language,lsp</Tags>
    <Categories>Programming Languages,Linters,Formatters</Categories>
    <GalleryFlags>Public</GalleryFlags>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="^1.75.0"/>
      <Property Id="Microsoft.VisualStudio.Code.ExtensionDependencies" Value=""/>
      <Property Id="Microsoft.VisualStudio.Code.ExtensionPack" Value=""/>
    </Properties>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code"/>
  </Installation>
  <Dependencies/>
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/>
  </Assets>
</PackageManifest>
"""


def main():
    # 顺序很重要：update.json 里记 vsix 的哈希，而 update.json 又要进 zip。
    # 4.7.2 修掉的自引用缺陷：旧顺序（先 zip 后 vsix）会让每跑一次 zip 的 SHA 都变，永不收敛。
    # 正确顺序：vsix → sha → update.json → 最后打 zip。
    vsix_name = f"vscode-impor-{VER}.vsix"
    vsix_path = os.path.join(OUT, vsix_name)
    ext_root = os.path.join(ROOT, "vscode-impor")
    with zipfile.ZipFile(vsix_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # 固定条目时间戳：既避免每次打出来的 SHA 都变（update.json 里记着它，
        # 而 update.json 又进 zip —— 会形成自引用、永不收敛），也让发布件可复现。
        for name, text in (("[Content_Types].xml", CONTENT_TYPES),
                           ("extension.vsixmanifest", MANIFEST.format(ver=VER))):
            zi = zipfile.ZipInfo(name, date_time=(2024, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(zi, text)
        for full, rel in iter_files(ext_root):
            rel_arc = "extension/" + rel.replace(os.sep, "/")
            st = os.stat(full)
            zi = zipfile.ZipInfo(rel_arc, date_time=(2024, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = (st.st_mode & 0xFFFF) << 16
            with open(full, "rb") as src:
                zf.writestr(zi, src.read())
    vsix_sha = sha256(vsix_path)
    print(f"vsix: {vsix_path}  bytes={os.path.getsize(vsix_path)}")

    # update.json：版本 + vsix 文件名 + 真实哈希（必须先写好，再打 zip）
    upd = os.path.join(ROOT, "site", "update.json")
    data = {
        "version": VER,
        "vsix": vsix_name,
        "url": "",
        "sha256": vsix_sha,
        "channel": "stable",
        "min_version": "2.8.0",
    }
    # 只在内容变化时落盘：update.json 会进 zip，mtime 一变 zip 的 SHA 就变，无法复现
    text = json.dumps(data, ensure_ascii=False)
    if not os.path.exists(upd) or open(upd, encoding="utf-8").read() != text:
        with open(upd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    print("update.json:", text)

    files = list(iter_files(ROOT))
    zip_name = f"Impor-{VER}-Windows-x64.zip"
    zip_path = os.path.join(OUT, zip_name)
    n = build_zip(zip_path, files)
    print(f"zip: {zip_path}  files={n}  bytes={os.path.getsize(zip_path)}")

    for path in (zip_path, vsix_path):
        digest = sha256(path)
        with open(path + ".sha256", "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f"{digest}  {os.path.basename(path)}\n")
        print(f"sha256 {os.path.basename(path)} = {digest}")


if __name__ == "__main__":
    main()
