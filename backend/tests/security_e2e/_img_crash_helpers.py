"""ADM-07 test yardımcıları: görsel dönüştürme ÇOCUK SÜRECİNDE çalışacak üst düzey fonksiyonlar.

Bilerek hafif tutulur (yalnız os/time): spawn edilen çocuk süreç bu modülü içe aktarır; test
harness'ı (conftest, mongomock…) çocukta yüklenmez.
"""
import os
import time


def hard_exit():
    """Yerel çökmeyi taklit eder: süreç temizlik yapmadan anında ölür."""
    os._exit(1)


def hard_abort():
    """SIGABRT (C düzeyinde abort) — segfault benzeri ani ölüm."""
    os.abort()


def hard_exit_any(*args, **kwargs):
    """Yükleme ucunun çağırdığı imzayla (data, edge, quality) çocukta ani ölüm."""
    os._exit(1)


def write_pid_then_sleep(path, sec):
    with open(path, "w") as f:
        f.write(str(os.getpid()))
    time.sleep(sec)
    return os.getpid()


def child_limits():
    """(RLIMIT_AS yumuşak tavanı, oom_score_adj) — çocuk süreçte okunur."""
    import resource
    try:
        with open("/proc/self/oom_score_adj") as f:
            adj = int(f.read().strip())
    except OSError:
        adj = None
    return resource.getrlimit(resource.RLIMIT_AS)[0], adj


def allocate_mb(n):
    """Bellek taşmasını taklit eder (tavan üstü ayırma)."""
    buf = bytearray(int(n) * 1024 * 1024)
    return len(buf)


def child_pid():
    return os.getpid()


def sleep_then_pid(sec):
    time.sleep(sec)
    return os.getpid()
