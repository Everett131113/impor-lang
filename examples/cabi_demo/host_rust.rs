// host_rust.rs —— Rust 宿主：直接链接 Impor 生成的 DLL
// 构建（MSVC 工具链）：rustc host_rust.rs -L . -l dylib=logic
// 构建（GNU 工具链）：rustc host_rust.rs -L . -l logic
// 注意：本机未安装 rustc，此文件仅作源码交付，未经本地验证（见 docs/ci-evidence.md）
use std::ffi::{CStr, CString};
use std::os::raw::c_char;

extern "C" {
    fn impor_add(a: i64, b: i64) -> i64;
    fn impor_fib(n: i64) -> i64;
    fn impor_greet(name: *const c_char) -> *const c_char;
    fn impor_free_str(s: *const c_char);
    fn impor_runtime_shutdown();
}

fn main() {
    unsafe {
        println!("Rust 宿主: impor_add(2,3)={}  impor_fib(15)={}", impor_add(2, 3), impor_fib(15));
        let name = CString::new("rust").unwrap();
        let s = impor_greet(name.as_ptr());
        println!("Rust 宿主: impor_greet(\"rust\")={}", CStr::from_ptr(s).to_string_lossy());
        impor_free_str(s);
        impor_runtime_shutdown();
    }
}
