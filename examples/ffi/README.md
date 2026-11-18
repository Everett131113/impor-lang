# SQLite FFI demo（v3.10）

这个目录验证 Impor 的 64 位 FFI 能完成一次真实的指针往返：

1. `sqlite3_open` 把 `sqlite3*` 写进输出缓冲区；
2. `sqlite3_exec` 建表并插入三行；
3. `sqlite3_prepare_v2` 把 `sqlite3_stmt*` 写进输出缓冲区；
4. `sqlite3_step` / `sqlite3_column_text` 逐行读字符串；
5. `sqlite3_finalize` / `sqlite3_close` 收尾。

## 运行

```powershell
python Impor.py run examples/ffi/sqlite_demo.impor
python Impor.py build examples/ffi/sqlite_demo.impor -o sqlite_demo.exe
.\sqlite_demo.exe
```

Windows 10/11 自带 `winsqlite3.dll`，不需要额外安装 SQLite。其他平台把脚本里的库名改为
`sqlite3.dll` 或 `libsqlite3.so` 即可；允许清单已同时登记。

## Windows 32 位注意事项

Windows 自带的 `winsqlite3.dll` 在 32 位下使用 `__stdcall`，所以脚本里每次 `ffi_call`
第 6 个参数显式传 `"stdcall"`；SQLite 的 `int` 参数（如 `sqlite3_prepare_v2` 的
`nByte`、`sqlite3_column_text` 的列号）必须用 `i32`，不能用会扩展成 8 字节的 `int`。
C 后端会据此生成 `IMPOR_STDCALL` 函数指针和精确的 32 位实参，避免 stdcall 栈失衡。

## 允许清单

`.impor/ffi_allow.json` 随这个目录交付，clone 后直接 build 即可：

```json
{"allow":["winsqlite3.dll","sqlite3.dll","libsqlite3.so","libsqlite3.dylib"]}
```

## 这不是通用指针 ABI

只新增了最小能力：

- `argtypes` 里的 `addr`：把整数地址（或 `ffi_alloc` 返回的缓冲区）作为 `void*` 传入；
- `ffi_alloc` / `ffi_read_i64` / `ffi_read_str_at`：分配不透明句柄缓冲区、读回句柄、读 NUL 结尾字符串；
- `ffi_call` 第 6 个参数 `"stdcall"`：Windows 32 位原生库的调用约定；不传则默认 `cdecl`。

不提供结构体布局、指针运算、回调或任意内存读写。SQLite demo 用到的 `ffi_alloc(8)` +
`ffi_read_i64` 只负责接住 `sqlite3*` / `sqlite3_stmt*` 这类不透明句柄。
