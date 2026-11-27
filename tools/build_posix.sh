#!/usr/bin/env bash
# Impor Linux / macOS 最小构建链（4.7.1）
#
# 用法：
#   tools/build_posix.sh examples/hello.impor                  # 本机默认编译器
#   tools/build_posix.sh foo.impor -o foo --static             # Linux 全静态（优先 musl-gcc）
#   CC=clang tools/build_posix.sh foo.impor                    # 指定编译器
#
# 说明：
#   - 只依赖 python3 + 一个 C 编译器，不需要 pip 包；
#   - 走完整后端（--no-fast），因为快路径只覆盖纯数值子集；
#   - macOS 上 `-static` 不受支持，脚本会降级并明确提示，而不是假装成功。
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PY=${PYTHON:-python3}
SRC=""
OUT=""
STATIC=0
CC_ARG=${CC:-}

die() { echo "错误：$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --static) STATIC=1; shift ;;
    -o) OUT=${2:-}; shift 2 ;;
    -o*) OUT=${1#-o}; shift ;;
    --cc) CC_ARG=${2:-}; shift 2 ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "未知参数 $1" ;;
    *) [ -z "$SRC" ] && SRC=$1 || die "只能给一个源码文件（多余：$1）"; shift ;;
  esac
done

[ -n "$SRC" ] || die "缺少源码文件：tools/build_posix.sh FILE.impor"
[ -f "$SRC" ] || die "源码不存在：$SRC"
command -v "$PY" >/dev/null 2>&1 || die "找不到 python3（可用 PYTHON=/path/to/python3 指定）"

OS=$(uname -s)
ARCH=$(uname -m)
echo "平台：$OS / $ARCH"

if [ -z "$CC_ARG" ]; then
  if [ "$OS" = "Darwin" ]; then CC_ARG=clang; else CC_ARG=gcc; fi
fi

if [ "$STATIC" -eq 1 ] && [ "$OS" = "Linux" ] && command -v musl-gcc >/dev/null 2>&1; then
  CC_ARG=musl-gcc
  echo "静态链接：使用 musl-gcc"
elif [ "$STATIC" -eq 1 ] && [ "$OS" = "Darwin" ]; then
  echo "提示：macOS 不支持静态链接，已忽略 --static（产物仍依赖系统 libSystem）" >&2
  STATIC=0
elif [ "$STATIC" -eq 1 ]; then
  echo "静态链接：使用 $CC_ARG -static（glibc 静态产物较大，建议装 musl-gcc）" >&2
fi

command -v "$CC_ARG" >/dev/null 2>&1 || die "找不到编译器 $CC_ARG（用 CC=... 或 --cc 指定）"
"$CC_ARG" --version 2>&1 | head -1

ARGS=(build "$SRC" --no-fast --cc "$CC_ARG")
[ "$STATIC" -eq 1 ] && ARGS+=(--static)
if [ -n "$OUT" ]; then ARGS+=(-o "$OUT"); fi

echo "+ $PY $ROOT/Impor.py ${ARGS[*]}"
(cd "$ROOT" && "$PY" "$ROOT/Impor.py" "${ARGS[@]}")

BIN=${OUT:-$(basename "${SRC%.impor}")}
case "$OS" in
  Darwin) BIN="$BIN" ;;                 # mac 上 cli 产出同名（无扩展名）
  *) BIN="$BIN" ;;
esac
if [ -f "$BIN" ]; then
  echo "产物：$BIN（$(wc -c < "$BIN" | tr -d ' ') 字节）"
else
  # cli 在 Windows 上补 .exe；Linux/mac 通常不补，这里兜一下
  for cand in "$BIN" "$BIN.exe" "$(dirname "$SRC")/$(basename "${SRC%.impor}")"; do
    [ -f "$cand" ] && { BIN="$cand"; break; }
  done
fi
[ -f "$BIN" ] || die "构建没有产出可执行文件（检查上面的日志）"

if [ "$OS" = "Linux" ] && command -v ldd >/dev/null 2>&1; then
  if ldd "$BIN" 2>&1 | grep -q "not a dynamic executable"; then
    echo "链接类型：全静态"
  else
    echo "链接类型：动态（ldd 前 3 行）"; ldd "$BIN" 2>&1 | head -3
  fi
fi

echo "冒烟运行："
"./$BIN" || { echo "运行失败（退出码 $?）" >&2; exit 1; }
echo "完成。"
