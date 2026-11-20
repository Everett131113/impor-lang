# 标准库：一个 import 就能用

3.12 新增六个模块，解释器和 C 后端同一套实现：

- `std.json`：parse / stringify / try_parse
- `std.fs`：read / write / exists / list / mkdir / move / size
- `std.path`：norm / join / basename / dirname / ext / stem
- `std.regex`：is_match / find_all / replace_all / contains
- `std.time`：readable / seconds / sleep_ms / elapsed
- `std.http`：get / get_json（用系统自带 curl，不装第三方包）

例子：

```
import std.json as json
var data = json.parse("[1,2]")
print(json.stringify(data))
```

这些模块也能被 `impor build` 编进 exe，在没有 Python 的机器上跑。
