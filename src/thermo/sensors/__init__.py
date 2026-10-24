"""센서 읽기. 각 모듈은 읽지 못한 값을 None 으로 두고 이유를 notes 에 남긴다."""

import ctypes


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False
