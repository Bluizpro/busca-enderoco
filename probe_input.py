import sys

print("tty?", sys.stdin.isatty())
try:
    v = input("digite algo e Enter: ")
    print("recebi:", repr(v))
except BaseException as e:
    print("estourou:", type(e).__name__)
