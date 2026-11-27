# atod：十进制字符串 -> double 的 Python 参考实现（正确舍入，round-to-even）。
# 用精确有理数（大整数）保证正确，对拍 Python float()。后续再设计机器码友好版本。
import struct


def parse_decimal(s):
    """返回 (sign, digits, exp10)，值 = sign * digits * 10**exp10。"""
    s = s.strip()
    sign = 1
    if s and s[0] in "+-":
        if s[0] == "-":
            sign = -1
        s = s[1:]
    mant = ""
    exp = 0
    if "e" in s or "E" in s:
        mant, es = s.replace("E", "e").split("e", 1)
        exp = int(es)
    else:
        mant = s
    if "." in mant:
        ip, fp = mant.split(".", 1)
        exp -= len(fp)
        mant = ip + fp
    else:
        mant = mant
    digits = int(mant) if mant else 0
    return sign, digits, exp


def _to_double(num, den):
    """有理数 num/den（均 >=0）-> 非负 double，正确舍入。"""
    if num == 0:
        return 0.0
    # E = floor(log2(num/den))
    if num >= den:
        E = 0
        while (den << (E + 1)) <= num:
            E += 1
    else:
        E = -1
        # v >= 2^E（E 负）等价 num * 2^(-E) >= den；左移 num 不丢精度。
        while (num << (-E)) < den:
            E -= 1
    # subnormal 最小指数 -1074；target_exp 钳到 -1022，由尾数舍入决定 0 或最小 subnormal。
    target_exp = max(E, -1022)
    shift = 52 - target_exp  # 把值缩放到尾数空间
    if shift >= 0:
        tn, td = num << shift, den
    else:
        tn, td = num, den << (-shift)
    q = tn // td
    r = tn % td
    # round-to-even
    if 2 * r > td:
        q += 1
    elif 2 * r == td and (q & 1):
        q += 1
    if target_exp == -1022:
        # subnormal / 边界：q 可能 < 2^52
        if q >= (1 << 53):           # 进位进入 normal
            q = 1 << 52
            target_exp = -1021
    else:
        if q >= (1 << 53):           # 尾数进位
            q = 1 << 52
            target_exp += 1
    if target_exp > 1023:
        return float("inf")
    if target_exp == -1022 and q < (1 << 52):
        bits = q                    # subnormal：expfield=0
    else:
        ef = target_exp + 1023
        mant = q & ((1 << 52) - 1)
        bits = (ef << 52) | mant
    return struct.unpack("<d", struct.pack("<Q", bits))[0]


def atod(s):
    sign, digits, exp10 = parse_decimal(s)
    if digits == 0:
        return -0.0 if sign < 0 else 0.0
    if exp10 >= 0:
        num = digits * (10 ** exp10)
        den = 1
    else:
        num = digits
        den = 10 ** (-exp10)
    v = _to_double(num, den)
    return -v if sign < 0 else v
