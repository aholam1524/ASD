def c_to_f(c):
    """Convert Celsius to Fahrenheit, rounded to one decimal place."""
    return round(c * 9 / 5 + 32, 1)


def f_to_c(f):
    """Convert Fahrenheit to Celsius, rounded to one decimal place."""
    return round((f - 32) * 5 / 9, 1)
