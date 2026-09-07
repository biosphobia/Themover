"""Windows-only fallback for sending HID output reports through the control pipe.

``hid_write`` (WriteFile on the interrupt pipe) is what psmoveapi and
PSMoveService use and it normally works, but some Bluetooth stacks only accept
output reports through ``HidD_SetOutputReport``.  This module opens the device
path a second time and uses that call.  Everything is best-effort: on any
failure the caller falls back to plain ``hid_write``.
"""
from __future__ import annotations

import sys
from typing import Optional


class ControlPipeWriter:
    def __init__(self, path: bytes | str) -> None:
        if not sys.platform.startswith("win"):
            raise RuntimeError("control-pipe writes are only available on Windows")
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.kernel32 = ctypes.windll.kernel32
        self.hid = ctypes.windll.hid
        text = path.decode("utf-8", "replace") if isinstance(path, bytes) else path
        GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
        FILE_SHARE_READ, FILE_SHARE_WRITE = 0x1, 0x2
        OPEN_EXISTING = 3
        self.kernel32.CreateFileW.restype = wintypes.HANDLE
        handle = self.kernel32.CreateFileW(text, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
        if handle in (None, -1, 0xFFFFFFFFFFFFFFFF):
            raise OSError(f"CreateFile failed for {text}: {ctypes.get_last_error()}")
        self.handle = handle
        self.report_length = self._output_report_length() or 49

    def _output_report_length(self) -> Optional[int]:
        ctypes = self.ctypes

        class HIDP_CAPS(ctypes.Structure):
            _fields_ = [
                ("Usage", ctypes.c_ushort), ("UsagePage", ctypes.c_ushort),
                ("InputReportByteLength", ctypes.c_ushort), ("OutputReportByteLength", ctypes.c_ushort),
                ("FeatureReportByteLength", ctypes.c_ushort), ("Reserved", ctypes.c_ushort * 17),
                ("NumberLinkCollectionNodes", ctypes.c_ushort),
                ("NumberInputButtonCaps", ctypes.c_ushort), ("NumberInputValueCaps", ctypes.c_ushort), ("NumberInputDataIndices", ctypes.c_ushort),
                ("NumberOutputButtonCaps", ctypes.c_ushort), ("NumberOutputValueCaps", ctypes.c_ushort), ("NumberOutputDataIndices", ctypes.c_ushort),
                ("NumberFeatureButtonCaps", ctypes.c_ushort), ("NumberFeatureValueCaps", ctypes.c_ushort), ("NumberFeatureDataIndices", ctypes.c_ushort),
            ]

        preparsed = ctypes.c_void_p()
        try:
            if not self.hid.HidD_GetPreparsedData(self.handle, ctypes.byref(preparsed)):
                return None
            caps = HIDP_CAPS()
            status = self.hid.HidP_GetCaps(preparsed, ctypes.byref(caps))
            if status != 0x00110000:  # HIDP_STATUS_SUCCESS
                return None
            return int(caps.OutputReportByteLength) or None
        except Exception:
            return None
        finally:
            if preparsed:
                self.hid.HidD_FreePreparsedData(preparsed)

    def write(self, report: bytes) -> int:
        buf = bytes(report) + bytes(max(0, self.report_length - len(report)))
        buf = buf[: max(self.report_length, len(report))]
        c_buf = self.ctypes.create_string_buffer(buf, len(buf))
        ok = self.hid.HidD_SetOutputReport(self.handle, c_buf, len(buf))
        return len(buf) if ok else -1

    def close(self) -> None:
        try:
            self.kernel32.CloseHandle(self.handle)
        except Exception:
            pass
