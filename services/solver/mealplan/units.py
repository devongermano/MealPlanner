"""Unit helpers and macro constants. Extracted verbatim from plan.py."""

MACROS = ("protein", "fat", "carb")
KCAL = {"protein": 4, "fat": 9, "carb": 4}
SHORT = {"protein": "p", "fat": "f", "carb": "c"}


def human_pack(g):
    """454g -> '1 lb'. Nobody shops in grams."""
    if g % 454 == 0 and g >= 454:
        n = g // 454
        return f"{n} lb" if n > 1 else "1 lb"
    if g in (227, 340, 425, 439, 473, 568, 780, 907, 946, 1360, 1814, 2270):
        oz = round(g / 28.35)
        return f"{oz} oz" if oz <= 34 else f"{oz/16:.1f} lb"
    return f"{g}g"


def fmt_miss(miss):
    return ", ".join(
        f"{SHORT[k]} forced {abs(v)}g OVER" if v > 0 else f"{SHORT[k]} {abs(v)}g SHORT"
        for k, v in miss.items())


def kcal_of(t):
    return sum(t[m] * KCAL[m] for m in MACROS)
