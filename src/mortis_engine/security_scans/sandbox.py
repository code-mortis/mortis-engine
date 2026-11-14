"""Launch the trusted scanner with Linux syscall enforcement of offline analysis."""

import ctypes
import errno
import os
import platform
import sys


def deny_network():
    architecture = {"x86_64": (0xC000003E, 41), "aarch64": (0xC00000B7, 198)}.get(platform.machine())
    if sys.platform != "linux" or architecture is None:
        raise RuntimeError("Offline security scanning requires a supported Linux worker.")

    class Instruction(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]

    class Program(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ushort), ("instructions", ctypes.POINTER(Instruction))]

    arch, socket = architecture
    # Check the ABI before syscall numbers, then reject socket creation, including DNS and module fetches.
    instructions = (Instruction * 8)(
        Instruction(0x20, 0, 0, 4), Instruction(0x15, 1, 0, arch), Instruction(0x06, 0, 0, 0x80000000),
        Instruction(0x20, 0, 0, 0), Instruction(0x15, 0, 1, socket),
        Instruction(0x06, 0, 0, 0x00050000 | errno.EPERM),
        Instruction(0x06, 0, 0, 0x7FFF0000), Instruction(0x06, 0, 0, 0x80000000),
    )
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) or libc.prctl(22, 2, ctypes.byref(Program(8, instructions)), 0, 0):
        raise RuntimeError("The security worker could not enforce offline scanner isolation.")


if __name__ == "__main__":
    deny_network()
    os.execvp(sys.argv[1], sys.argv[1:])
