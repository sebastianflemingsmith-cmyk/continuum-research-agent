"""Run a Python entry point with outbound sockets blocked, including in tests."""
import runpy
import socket
import sys


def blocked(*args, **kwargs):
    raise RuntimeError("Network access is disabled for this offline demonstration/test.")


def block_network():
    socket.getaddrinfo = blocked
    socket.gethostbyname = blocked
    socket.gethostbyname_ex = blocked
    socket.create_connection = blocked
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.socket.sendto = blocked


if __name__ == "__main__":
    block_network()
    sys.argv = sys.argv[1:]
    sys.path.insert(0, str(__import__("pathlib").Path(sys.argv[0]).resolve().parent))
    runpy.run_path(sys.argv[0], run_name="__main__")
