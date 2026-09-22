from temp.convert import c_to_f, f_to_c


def test_zero_celsius_to_fahrenheit():
    assert c_to_f(0) == 32.0


def test_freezing_fahrenheit_to_celsius():
    assert f_to_c(32) == 0.0


def test_boiling_celsius_to_fahrenheit():
    assert c_to_f(100) == 212.0


def test_boiling_fahrenheit_to_celsius():
    assert f_to_c(212) == 100.0
