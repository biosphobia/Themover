"""Windows-only helpers for sending HID output reports through the control pipe.

``hid_write`` (WriteFile on the interrupt pipe) is what psmoveapi and
PSMoveService use, but on some Bluetooth stacks / controller models the
output report is only accepted through ``HidD_SetOutputReport`` on a specific
HID collection.  :class:`ControlPipeWriter` opens one collection path and
sends through that call, and also reports the collection's report sizes so
diagnostics can show which collection actually carries an output report.
Everything is best-effort: on any failure the caller falls back.
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
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.hid = ctypes.WinDLL("hid", use_last_error=True)
        text = path.decode("utf-8", "replace") if isinstance(path, bytes) else path
        self.path = text
        GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
        FILE_SHARE_READ, FILE_SHARE_WRITE = 0x1, 0x2
        OPEN_EXISTING = 3
        self.kernel32.CreateFileW.restype = wintypes.HANDLE
        self.kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        handle = self.kernel32.CreateFileW(text, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
        if handle in (None, -1, 0xFFFFFFFFFFFFFFFF):
            raise OSError(f"CreateFile failed for {text}: error {ctypes.get_last_error()}")
        self.handle = handle
        self.hid.HidD_SetOutputReport.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
        self.hid.HidD_SetOutputReport.restype = wintypes.BOOLEAN
        self.input_length, self.output_length, self.feature_length = self._caps()
        self.report_length = self.output_length or 49
        self.last_error = 0

    def _caps(self) -> tuple[int, int, int]:
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
                return (0, 0, 0)
            caps = HIDP_CAPS()
            status = self.hid.HidP_GetCaps(preparsed, ctypes.byref(caps))
            if status != 0x00110000:  # HIDP_STATUS_SUCCESS
                return (0, 0, 0)
            return (int(caps.InputReportByteLength), int(caps.OutputReportByteLength), int(caps.FeatureReportByteLength))
        except Exception:
            return (0, 0, 0)
        finally:
            if preparsed:
                self.hid.HidD_FreePreparsedData(preparsed)

    def describe(self) -> str:
        return f"in={self.input_length} out={self.output_length} feature={self.feature_length}"

    def write(self, report: bytes) -> int:
        buf = bytes(report) + bytes(max(0, self.report_length - len(report)))
        buf = buf[: max(self.report_length, len(report))]
        c_buf = self.ctypes.create_string_buffer(buf, len(buf))
        ok = self.hid.HidD_SetOutputReport(self.handle, c_buf, len(buf))
        if not ok:
            self.last_error = self.ctypes.get_last_error()
            return -1
        return len(buf)

    def close(self) -> None:
        try:
            self.kernel32.CloseHandle(self.handle)
        except Exception:
            pass


def collection_caps(path: bytes | str) -> Optional[str]:
    """'in=.. out=.. feature=..' for one HID collection path (None off Windows)."""
    try:
        w = ControlPipeWriter(path)
    except Exception as exc:
        return f"unavailable ({exc})" if sys.platform.startswith("win") else None
    try:
        return w.describe()
    finally:
        w.close()
