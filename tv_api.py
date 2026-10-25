"""Talks to a Samsung Smart TV (2016 and later, Tizen) over the local network.

Uses only the standard library: the TV's REST info endpoint on port 8001, its remote-control
WebSocket (secure on 8002, plain on 8001), and Wake-on-LAN for turning the TV on.
"""
import base64
import json
import os
import queue
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.request

import tv_logic as logic


class TVError(Exception):
    pass


class NotAllowed(TVError):
    pass


# ---------------------------------------------------------------- minimal WebSocket client

class WebSocket:
    """Just enough RFC 6455 for the TV: masked text frames out, text/ping/close in."""

    def __init__(self, host, port, path, secure, timeout=10):
        raw = socket.create_connection((host, port), timeout=timeout)
        if secure:
            ctx = ssl.create_default_context()
            # The TV uses a self-signed certificate.
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            raw = ctx.wrap_socket(raw, server_hostname=host)
        self.sock = raw
        self.send_lock = threading.Lock()
        key = base64.b64encode(os.urandom(16)).decode()
        request = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key))
        self.sock.sendall(request.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise TVError("The TV closed the connection during setup.")
            head += chunk
        head, self._buf = head.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status + b" ":
            raise TVError("The TV refused the remote connection (%s)." % status.decode(errors="replace"))

    def _read(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("connection closed")
            self._buf += chunk
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def _send_frame(self, opcode, payload):
        header = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self.send_lock:
            self.sock.sendall(header + mask + masked)

    def send(self, text):
        self._send_frame(0x1, text.encode("utf-8"))

    def recv(self):
        """Next text message. Answers pings; raises ConnectionError when the TV closes."""
        message = b""
        while True:
            b0, b1 = self._read(2)
            opcode, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            payload = self._read(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise ConnectionError("the TV closed the connection")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode in (0x0, 0x1, 0x2):
                message += payload
                if b0 & 0x80:
                    return message.decode("utf-8", errors="replace")

    def close(self):
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------- the TV

class SamsungTV:
    def __init__(self, host, mac="", token="", on_token=None):
        self.host = host
        self.mac = mac
        self.token = token
        self.on_token = on_token      # called with a new token so the app can save it
        self.ws = None
        self.events = queue.Queue()
        self.lock = threading.RLock()

    # ---- REST (works while the TV is on, and on many models in standby)

    def info(self, timeout=3):
        """The TV's description, or None when it doesn't answer (off, or wrong address)."""
        try:
            with urllib.request.urlopen("http://%s:8001/api/v2/" % self.host, timeout=timeout) as r:
                return json.load(r)
        except (OSError, ValueError):
            return None

    def app_status(self, app_id, timeout=3):
        """{'id','name','running','visible'} when the app is installed, else None."""
        try:
            with urllib.request.urlopen("http://%s:8001/api/v2/applications/%s" % (self.host, app_id),
                                        timeout=timeout) as r:
                return json.load(r)
        except (OSError, ValueError):
            return None

    def _rest(self, method, app_id):
        req = urllib.request.Request("http://%s:8001/api/v2/applications/%s" % (self.host, app_id),
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status < 300
        except urllib.error.HTTPError:
            return False
        except OSError as e:
            raise TVError("The TV did not answer: %s" % e)

    def close_app(self, app_id):
        return self._rest("DELETE", app_id)

    # ---- remote-control WebSocket

    @property
    def connected(self):
        return self.ws is not None

    def connect(self, wait_for_allow=35):
        """Open the remote channel. The first time, the TV asks on screen to allow this app."""
        with self.lock:
            if self.ws:
                return
            errors = []
            for port, secure in ((8002, True), (8001, False)):
                try:
                    ws = WebSocket(self.host, port, logic.remote_path(self.token if secure else ""), secure)
                except (OSError, TVError) as e:
                    errors.append(e)
                    continue
                ws.sock.settimeout(wait_for_allow)
                try:
                    reply = json.loads(ws.recv())
                except (OSError, ConnectionError, ValueError) as e:
                    ws.close()
                    raise TVError("The TV did not confirm the connection. If it asked to allow "
                                  "a new device, accept it and try again. (%s)" % e)
                event = reply.get("event")
                if event == "ms.channel.unauthorized":
                    ws.close()
                    raise NotAllowed("The TV refused this computer. On the TV, go to Settings, "
                                     "General, External Device Manager, Device Connection Manager, "
                                     "Device List, and allow or remove 'Accessible TV Remote', then try again.")
                if event == "ms.channel.timeOut":
                    ws.close()
                    raise NotAllowed("Nobody accepted the connection request on the TV in time. "
                                     "Try again and accept it with the TV remote.")
                if event != "ms.channel.connect":
                    ws.close()
                    raise TVError("Unexpected answer from the TV: %s" % event)
                token = (reply.get("data") or {}).get("token")
                if token and token != self.token:
                    self.token = token
                    if self.on_token:
                        self.on_token(token)
                ws.sock.settimeout(None)
                self.ws = ws
                self.events = queue.Queue()
                threading.Thread(target=self._reader, args=(ws,), daemon=True).start()
                return
            raise TVError("Could not reach the TV at %s. Is it on, and is the address right? (%s)"
                          % (self.host, errors[-1] if errors else "no answer"))

    def _reader(self, ws):
        try:
            while True:
                try:
                    self.events.put(json.loads(ws.recv()))
                except ValueError:
                    pass
        except (OSError, ConnectionError):
            pass
        with self.lock:
            if self.ws is ws:
                self.ws = None

    def disconnect(self):
        with self.lock:
            if self.ws:
                self.ws.close()
                self.ws = None

    def _send(self, payload):
        """Send on the open channel, reconnecting once if the TV dropped it."""
        for attempt in (1, 2):
            self.connect()
            try:
                self.ws.send(json.dumps(payload))
                return
            except (OSError, AttributeError):
                self.disconnect()
                if attempt == 2:
                    raise TVError("Lost the connection to the TV.")

    def send_key(self, key, cmd="Click"):
        self._send(logic.key_payload(key, cmd))

    def hold_key(self, key, seconds):
        self.send_key(key, "Press")
        time.sleep(seconds)
        self.send_key(key, "Release")

    def send_text(self, text):
        """Type into the TV's on-screen keyboard (it must be showing)."""
        self._send(logic.text_payload(text))

    def _wait_event(self, name, timeout):
        end = time.time() + timeout
        while time.time() < end:
            try:
                ev = self.events.get(timeout=max(0.05, end - time.time()))
            except queue.Empty:
                break
            if ev.get("event") == name:
                return ev
        return None

    def installed_apps(self):
        """[(name, app_id, app_type)] from the TV, or from probing well-known apps if it won't list them."""
        self.connect()
        while not self.events.empty():
            self.events.get_nowait()
        self._send(logic.emit_payload("ed.installedApp.get"))
        ev = self._wait_event("ed.installedApp.get", 3)
        apps = logic.parse_installed_apps(ev) if ev else []
        if apps:
            return apps
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(8) as ex:
            statuses = list(ex.map(lambda a: self.app_status(a[1]), logic.KNOWN_APPS))
        return [((status.get("name") or name), app_id, 2)
                for (name, app_id), status in zip(logic.KNOWN_APPS, statuses) if status]

    def launch_app(self, app_id, app_type=2):
        # Newer TVs (2021 Frame included) silently ignore ed.apps.launch but honour the REST call.
        try:
            if self._rest("POST", app_id):
                return
        except TVError:
            pass
        self._send(logic.launch_payload(app_id, app_type))

    def open_browser(self, url):
        self._send(logic.launch_payload("org.tizen.browser", 4, url))

    # ---- power

    def wake(self):
        """Send Wake-on-LAN. Needs the TV's MAC address and 'Power On with Mobile' enabled."""
        if not self.mac:
            raise TVError("The TV's network (MAC) address isn't known yet, so it can't be woken. "
                          "Set it in Settings.")
        packet = logic.magic_packet(self.mac)
        targets = {"255.255.255.255", logic.broadcast_for(self.host)}
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for _ in range(3):
                for t in targets:
                    for port in (9, 7):
                        try:
                            s.sendto(packet, (t, port))
                        except OSError:
                            pass
                time.sleep(0.1)


def local_addresses():
    """This computer's IPv4 LAN addresses, the one with the default route first.

    A VPN or virtual adapter (Hyper-V, VirtualBox) can own the default route, so every
    private address is returned, not just that one.
    """
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        found += [a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    result = []
    for ip in found:
        if logic.is_lan_address(ip) and ip not in result:
            result.append(ip)
    return result


def find_tvs(timeout=1.5, retry_timeout=4):
    """Look for Samsung TVs on every network this computer is on.

    Returns (found, searched): found is [(ip, info dict)], searched the networks scanned,
    like ['192.168.8'], for explaining a failure.
    """
    import concurrent.futures

    addresses = local_addresses()
    prefixes = []
    for ip in addresses:
        prefix = ip.rsplit(".", 1)[0]
        if prefix not in prefixes:
            prefixes.append(prefix)

    def is_tv(ip):
        info = SamsungTV(ip).info(timeout=4)
        return (ip, info) if info and "device" in info else None

    def probe(ip, timeout=timeout):
        s = socket.socket()
        s.settimeout(timeout)
        try:
            if s.connect_ex((ip, 8001)) != 0:
                return None
        except OSError:
            return None
        finally:
            s.close()
        return is_tv(ip)

    candidates = ["%s.%d" % (p, i) for p in prefixes for i in range(1, 255)]
    candidates = [ip for ip in candidates if ip not in addresses]
    with concurrent.futures.ThreadPoolExecutor(128) as ex:
        found = [r for r in ex.map(probe, candidates) if r]
        if not found:
            # A TV on Wi-Fi can be slow to answer, especially in standby: try once more, slower.
            found = [r for r in ex.map(lambda ip: probe(ip, retry_timeout), candidates) if r]
    return found, prefixes
