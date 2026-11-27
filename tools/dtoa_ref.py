# 最短浮点打印算法（Burger-Dijkstra 思想）的 Python 参考实现。
# 目标：对任意有限 double，输出与 Python str(float) 完全一致的最短十进制串。
# 全程整数运算（含 10 的幂表），便于后续逐行移植到 x64 机器码运行时。
import struct

# 10 的整数幂表（10^0 .. 10^325）
POW10 = [1]
for _i in range(325):
    POW10.append(POW10[-1] * 10)


def _decompose(v):
    """返回 (f, e, spec)：v == f * 2**e，f 为整数尾数。"""
    bits = struct.unpack("<Q", struct.pack("<d", v))[0]
    mant = bits & ((1 << 52) - 1)
    expfield = (bits >> 52) & 0x7FF
    if expfield == 0:
        return mant, 1 - 1023 - 52, False
    return mant | (1 << 52), expfield - 1023 - 52, True


def _find_p(f, e):
    """p = floor(log10(v))，用 10 的幂表做整数比较定位（不用 log）。"""
    # 先取一个很接近的候选，再用整数比较校正。
    import math
    p = math.floor(math.log10(f) + e * math.log10(2))

    def ge_power(p):  # v >= 10**p ?
        if p >= 0:
            ten = POW10[p]
            if e >= 0:
                return (f << e) >= ten
            return f >= ten << (-e)
        t = POW10[-p]
        if e >= 0:
            return (f << e) * t >= 1
        return f * t >= 1 << (-e)

    while p > -324 and not ge_power(p):
        p -= 1
    while ge_power(p + 1):
        p += 1
    return p


def _carry(digits, d, p):
    """当前位 d 需向上 +1 进位（d==9 时 d+1=10）。返回 (新数字序列, 新 p)。"""
    work = digits + [d]
    j = len(work) - 1
    while j >= 0 and work[j] == 9:
        work[j] = 0
        j -= 1
    if j < 0:                 # 首位溢出：9.9.. -> 10.0..，最高位权 +1
        work.insert(0, 1)
        p += 1
    else:
        work[j] += 1
    while len(work) > 1 and work[-1] == 0:   # 去掉进位产生的冗余尾 0
        work.pop()
    return work, p


def _digits(v):
    """返回 (digit_list, p)：最高位权为 10**p，数字是 v/10**p 的十进制展开。"""
    f, e, spec = _decompose(v)
    is_pow2 = spec and f == (1 << 52)
    # (r, s, ml, mr)：r/s = v；ml/s、mr/s 是 v 到下/上舍入边界（中点）的距离。
    # 普通数边界在 0.5 ulp；精确 2 的幂下边界 0.25 ulp、上边界 0.5 ulp。
    # 把 r、s 整体 ×2 使中点距离落到整数 ml=mr=1。
    if e >= 0:
        if not is_pow2:
            r = f << (e + 1)
            s = 2
            ml = mr = 1 << e
        else:
            r = f << (e + 2)
            s = 4
            ml = 1 << e
            mr = 1 << (e + 1)
    else:
        if not is_pow2:
            r = f << 1
            s = 1 << (-e + 1)
            ml = mr = 1
        else:
            r = f << 2
            s = 1 << (-e + 2)
            ml = 1
            mr = 2
    p = _find_p(f, e)
    # 缩放使 m = v/10^(p+1) ∈ [0.1, 1)。距离按相同比例缩放。
    q = p + 1
    if q >= 0:
        s *= POW10[q]
    else:
        z = POW10[-q]
        r *= z
        ml *= z
        mr *= z

    digits = []
    while True:
        r *= 10
        ml *= 10
        mr *= 10
        d = r // s
        r = r % s
        # 截断值恰在舍入边界（中点）时按 round-to-even：尾数 f 为偶数则中点
        # 舍入到 v（包含边界），f 为奇数则舍入到偶数编号的相邻 double（严格排除）。
        even = (f & 1) == 0
        low_ok = (r <= ml) if even else (r < ml)
        high_ok = ((s - r) <= mr) if even else ((s - r) < mr)
        if not (low_ok or high_ok):
            digits.append(d)
            continue
        # 终止：先选出最后一位 final_d（可能为 10），再统一处理进位。
        if low_ok and high_ok:
            if 2 * r > s:
                final_d = d + 1
            elif 2 * r == s and (d & 1):   # 等距，舍入到偶数
                final_d = d + 1
            else:
                final_d = d
        elif high_ok:
            final_d = d + 1
        else:
            final_d = d
        if final_d >= 10:         # 跨十进制幂边界（含首位溢出），提升 p
            digits, p = _carry(digits, 9, p)
        else:
            digits.append(final_d)
        break
    return digits, p


def format_float(v):
    if v != v:
        return "nan"
    if v == float("inf"):
        return "inf"
    if v == float("-inf"):
        return "-inf"
    if v == 0:
        return "-0.0" if struct.unpack("<Q", struct.pack("<d", v))[0] >> 63 else "0.0"
    neg = v < 0
    digits, p = _digits(abs(v))
    out = _render(digits, p)
    return ("-" + out) if neg else out


def _render(digits, p):
    # 整数部分位数 = p+1
    exp10 = p
    if -4 <= exp10 <= 15:
        return _fixed(digits, p + 1)
    return _sci(digits, exp10)


def _fixed(digits, k):  # k = 整数位数
    s = "".join(str(d) for d in digits)
    if k <= 0:
        return "0." + "0" * (-k) + s
    if k >= len(s):
        return s + "0" * (k - len(s)) + ".0"
    return s[:k] + "." + s[k:]


def _sci(digits, exp10):
    s = "".join(str(d) for d in digits)
    head = s[0]
    rest = s[1:]
    mant = head + ("." + rest if rest else "")
    es = ("+" if exp10 >= 0 else "-") + str(abs(exp10)).zfill(2)
    return mant + "e" + es
