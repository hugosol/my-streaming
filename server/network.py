import socket
import random


def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(0)
        s.connect(("10.254.254.254", 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def find_port(preferred=8888):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", preferred)) != 0:
                return preferred
    except Exception:
        pass
    ports = list(range(49152, 65535))
    random.shuffle(ports)
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No free ports available")


def find_free_port(start=49152, end=65535):
    ports = list(range(start, end))
    random.shuffle(ports)
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No free ports available")


def start_mdns(ip, port, name="my-streaming"):
    from zeroconf import Zeroconf, ServiceInfo
    info = ServiceInfo(
        "_http._tcp.local.",
        f"{name}._http._tcp.local.",
        addresses=[socket.inet_aton(ip)],
        port=port,
        properties={"path": "/"},
        server=f"{name}.local.",
    )
    zc = Zeroconf()
    zc.register_service(info)
    return zc, info


def stop_mdns(zc, info):
    import asyncio
    async def _cleanup():
        await zc.async_unregister_service(info)
        await zc.async_close()
    try:
        asyncio.run(_cleanup())
    except Exception:
        pass
