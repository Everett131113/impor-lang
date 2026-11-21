"""宿主语言（Python 64 位）通过 ctypes 调用 Impor 编出的 64 位 DLL。"""
import ctypes, sys, os

dll_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "cabi_probe.dll")
lib = ctypes.CDLL(dll_path)

lib.impor_add.argtypes = [ctypes.c_longlong, ctypes.c_longlong]
lib.impor_add.restype = ctypes.c_longlong
lib.impor_fib.argtypes = [ctypes.c_longlong]
lib.impor_fib.restype = ctypes.c_longlong
lib.impor_greet.argtypes = [ctypes.c_char_p]
lib.impor_greet.restype = ctypes.c_char_p

print("Python 宿主: impor_add(2,3) =", lib.impor_add(2, 3))
print("Python 宿主: impor_fib(15)  =", lib.impor_fib(15))
print("Python 宿主: impor_greet('rust') =", lib.impor_greet(b"rust").decode())
print("Python 宿主: 退出前调用 impor_runtime_shutdown()")
lib.impor_runtime_shutdown()
print("ok")
