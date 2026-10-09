"""Run focused pytest under an inherited Linux kernel network prohibition.

This supporting investigation fixture fails closed on unsupported platforms.
AF_UNIX socket pairs remain available for asyncio's local wake-up pipe. New
network sockets and all connect syscalls are denied, including in Node children.
No credentials, environment configuration, or production settings are changed.
"""
from __future__ import annotations

import ctypes
import errno
import os
import platform
import runpy
import socket
import sys
from pathlib import Path


def block_network() -> None:
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise RuntimeError("This offline launcher requires Linux x86_64 seccomp")

    class Filter(ctypes.Structure):
        _fields_ = [
            ("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
            ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32),
        ]

    class Program(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ushort), ("filters", ctypes.POINTER(Filter))]

    # seccomp_data: syscall number at 0, audit architecture at 4, arg[0] at 16.
    # Reject other ABIs (including x32), connect (42), and socket/socketpair
    # (41/53) unless their domain is AF_UNIX. Filter is inherited across exec.
    denied = 0x00050000 | errno.EPERM
    instructions = [
        (0x20, 0, 0, 4),
        (0x15, 1, 0, 0xC000003E),
        (0x06, 0, 0, denied),
        (0x20, 0, 0, 0),
        (0x45, 0, 1, 0x40000000),
        (0x06, 0, 0, denied),
        (0x15, 0, 1, 42),
        (0x06, 0, 0, denied),
        (0x15, 1, 0, 41),
        (0x15, 0, 3, 53),
        (0x20, 0, 0, 16),
        (0x15, 1, 0, socket.AF_UNIX),
        (0x06, 0, 0, denied),
        (0x06, 0, 0, 0x7FFF0000),
    ]
    filters = (Filter * len(instructions))(*(Filter(*row) for row in instructions))
    program = Program(len(filters), filters)
    libc = ctypes.CDLL(None, use_errno=True)
    for option, value in ((38, 1), (22, 2)):  # NO_NEW_PRIVS, SECCOMP_MODE_FILTER
        argument = ctypes.byref(program) if option == 22 else 0
        if libc.prctl(option, value, argument, 0, 0) != 0:
            code = ctypes.get_errno()
            raise OSError(code, "Cannot install mandatory offline seccomp filter")

    # These checks create no network sockets and initiate no connection.
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            unexpected = socket.socket(family, socket.SOCK_STREAM)
        except PermissionError as error:
            assert error.errno == errno.EPERM
        else:
            unexpected.close()
            raise AssertionError("Kernel network prohibition is ineffective")
    with socket.socket(socket.AF_UNIX) as local:
        try:
            local.connect("/tmp/offline-investigation-nonexistent.socket")
        except PermissionError as error:
            assert error.errno == errno.EPERM
        else:
            raise AssertionError("Kernel connect prohibition is ineffective")
    first, second = socket.socketpair()
    with first, second:
        assert first.family == second.family == socket.AF_UNIX
    print("Kernel network guard PASS: IPv4/IPv6 sockets and connect denied", flush=True)


def support_local_asyncio_wakeup() -> None:
    """Use local write(2) when the outer sandbox denies socket send(2).

    This is limited to asyncio's already-created AF_UNIX wake-up socket. It
    cannot open a network socket or connect, and only affects this test process.
    Without it, real mocked to_thread work completes but cannot wake the loop.
    """
    import asyncio

    def write_to_self(loop):
        local = loop._csock
        if local is None:
            return
        if local.family != socket.AF_UNIX:
            raise AssertionError("Asyncio wake-up must use local AF_UNIX only")
        try:
            os.write(local.fileno(), b"\0")
        except OSError:
            # Match asyncio's handling of a closed/full local wake-up pipe.
            pass

    asyncio.SelectorEventLoop._write_to_self = write_to_self

    async def check():
        # This uses a real thread and no network; failure cannot hang indefinitely.
        assert await asyncio.wait_for(asyncio.to_thread(lambda: True), timeout=1)

    asyncio.run(check())
    print("Local asyncio thread wake-up PASS: AF_UNIX write only", flush=True)


if __name__ == "__main__":
    block_network()
    support_local_asyncio_wakeup()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.argv = ["pytest", *sys.argv[1:]]
    runpy.run_module("pytest", run_name="__main__")
