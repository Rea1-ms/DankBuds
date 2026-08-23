#!/usr/bin/env python3

import json
import os
import signal
import sys
import unicodedata

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib


BLUEZ_SERVICE = "org.bluez"
OBJECT_MANAGER_INTERFACE = "org.freedesktop.DBus.ObjectManager"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
DEVICE_INTERFACE = "org.bluez.Device1"
PROFILE_MANAGER_INTERFACE = "org.bluez.ProfileManager1"
PROFILE_INTERFACE = "org.bluez.Profile1"
PROFILE_PATH = "/io/github/rea1ms/DankBuds/Profile"
SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"

SESSION_BUS_NAME = "io.github.rea1ms.DankBuds"
SESSION_OBJECT_PATH = "/io/github/rea1ms/DankBuds"
SESSION_INTERFACE = "io.github.rea1ms.DankBuds1"

HANDSHAKE = bytes.fromhex("ff010000000a0300")
QUERY_BATTERY = bytes.fromhex("ff040000001d1a01")
QUERY_NOISE_MODE = bytes.fromhex("ff040000001d1003")
NOISE_WRITE_VALUES = {"off": 0x01, "anc": 0x02, "transparency": 0x04}
NOISE_QUERY_VALUES = {0x00: "off", 0x01: "anc", 0x02: "transparency"}


def encode_frame(command, subcommand, opcode, parameters=b"", message_type=0x04):
    length = len(parameters)
    return bytes((0xFF, message_type, 0x00, length & 0xFF, length >> 8, command, subcommand, opcode)) + parameters


def normalized_name(name):
    return "".join(character for character in unicodedata.normalize("NFKC", name).casefold() if character.isalnum())


def is_robin_name(name):
    normalized = normalized_name(str(name or ""))
    return normalized == "robinsearphones" or "moondroprobin" in normalized or "水月雨知更鸟" in normalized


class RobinDecoder:
    def __init__(self):
        self.pending = bytearray()

    def offer(self, data):
        self.pending.extend(data)
        frames = []
        while len(self.pending) >= 8:
            try:
                marker = self.pending.index(0xFF)
            except ValueError:
                self.pending.clear()
                break
            if marker:
                del self.pending[:marker]
            if len(self.pending) < 8:
                break
            if self.pending[1] != 0x04 or self.pending[2] != 0x00:
                del self.pending[0]
                continue
            payload_length = self.pending[3] | self.pending[4] << 8
            if payload_length > 256:
                del self.pending[0]
                continue
            frame_length = 8 + payload_length
            if len(self.pending) < frame_length:
                break
            raw = bytes(self.pending[:frame_length])
            del self.pending[:frame_length]
            frames.append((raw[5], raw[6], raw[7], raw[8:]))
        return frames

    def reset(self):
        self.pending.clear()


class SessionApi(dbus.service.Object):
    def __init__(self, bus_name, controller):
        super().__init__(bus_name, SESSION_OBJECT_PATH)
        self.controller = controller

    @dbus.service.method(SESSION_INTERFACE, in_signature="", out_signature="s")
    def GetState(self):
        return self.controller.serialized_state()

    @dbus.service.method(SESSION_INTERFACE, in_signature="s", out_signature="")
    def SetNoiseMode(self, mode):
        self.controller.set_noise_mode(str(mode))

    @dbus.service.method(SESSION_INTERFACE, in_signature="", out_signature="")
    def Refresh(self):
        self.controller.refresh()

    @dbus.service.signal(SESSION_INTERFACE, signature="s")
    def StateChanged(self, state):
        return state


class BluezProfile(dbus.service.Object):
    def __init__(self, bus, controller):
        super().__init__(bus, PROFILE_PATH)
        self.controller = controller

    @dbus.service.method(PROFILE_INTERFACE, in_signature="", out_signature="")
    def Release(self):
        self.controller.detach_channel(False)

    @dbus.service.method(PROFILE_INTERFACE, in_signature="oha{sv}", out_signature="")
    def NewConnection(self, device_path, file_descriptor, properties):
        descriptor = file_descriptor.take()
        self.controller.accept_channel(str(device_path), descriptor)

    @dbus.service.method(PROFILE_INTERFACE, in_signature="o", out_signature="")
    def RequestDisconnection(self, device_path):
        if str(device_path) == self.controller.device_path:
            self.controller.detach_channel(False)


class RobinController:
    def __init__(self, system_bus):
        self.system_bus = system_bus
        self.api = None
        self.profile = None
        self.profile_manager = None
        self.profile_registered = False
        self.device_path = ""
        self.file_descriptor = -1
        self.channel_watch = 0
        self.decoder = RobinDecoder()
        self.handshake_accepted = False
        self.connection_timer = 0
        self.connection_attempt = 0
        self.handshake_timer = 0
        self.poll_timer = 0
        self.battery_timer = 0
        self.battery_attempt = 0
        self.battery_committed = False
        self.noise_timer = 0
        self.noise_attempt = 0
        self.expected_mode = ""
        self.state = {
            "connected": False,
            "ready": False,
            "name": "MOONDROP Robin",
            "left": None,
            "right": None,
            "mode": None,
            "error": "",
        }

    def set_api(self, api):
        self.api = api

    def serialized_state(self):
        return json.dumps(self.state, ensure_ascii=False, separators=(",", ":"))

    def update_state(self, **changes):
        changed = False
        for key, value in changes.items():
            if self.state.get(key) != value:
                self.state[key] = value
                changed = True
        if changed and self.api:
            self.api.StateChanged(self.serialized_state())

    def start(self):
        self.profile = BluezProfile(self.system_bus, self)
        self.profile_manager = dbus.Interface(
            self.system_bus.get_object(BLUEZ_SERVICE, "/org/bluez"),
            PROFILE_MANAGER_INTERFACE,
        )
        self.profile_manager.RegisterProfile(
            PROFILE_PATH,
            SPP_UUID,
            {
                "Name": dbus.String("Dank Buds Robin"),
                "Role": dbus.String("client"),
                "AutoConnect": dbus.Boolean(False),
            },
        )
        self.profile_registered = True
        self.system_bus.add_signal_receiver(
            self.properties_changed,
            dbus_interface=PROPERTIES_INTERFACE,
            signal_name="PropertiesChanged",
            path_keyword="path",
        )
        self.system_bus.add_signal_receiver(
            self.interfaces_changed,
            dbus_interface=OBJECT_MANAGER_INTERFACE,
            signal_name="InterfacesAdded",
        )
        self.system_bus.add_signal_receiver(
            self.interfaces_changed,
            dbus_interface=OBJECT_MANAGER_INTERFACE,
            signal_name="InterfacesRemoved",
        )
        self.evaluate_devices()

    def stop(self):
        self.clear_timer("connection_timer")
        self.clear_timer("handshake_timer")
        self.clear_timer("poll_timer")
        self.clear_timer("battery_timer")
        self.clear_timer("noise_timer")
        self.detach_channel(False)
        if self.profile_registered:
            try:
                self.profile_manager.UnregisterProfile(PROFILE_PATH)
            except dbus.DBusException:
                pass
            self.profile_registered = False

    def managed_devices(self):
        manager = dbus.Interface(
            self.system_bus.get_object(BLUEZ_SERVICE, "/"),
            OBJECT_MANAGER_INTERFACE,
        )
        return manager.GetManagedObjects()

    def evaluate_devices(self):
        candidate_path = ""
        candidate_name = ""
        try:
            managed = self.managed_devices()
        except dbus.DBusException as error:
            self.update_state(error=f"Bluetooth unavailable: {error.get_dbus_name()}")
            return
        for path, interfaces in managed.items():
            properties = interfaces.get(DEVICE_INTERFACE)
            if not properties or not bool(properties.get("Connected", False)):
                continue
            names = (properties.get("Name", ""), properties.get("Alias", ""))
            matched_name = next((str(name) for name in names if is_robin_name(name)), "")
            if matched_name:
                candidate_path = str(path)
                candidate_name = matched_name
                break
        if not candidate_path:
            self.device_path = ""
            self.detach_channel(False)
            self.update_state(
                connected=False,
                ready=False,
                name="MOONDROP Robin",
                left=None,
                right=None,
                mode=None,
                error="",
            )
            return
        if candidate_path != self.device_path:
            self.detach_channel(False)
            self.device_path = candidate_path
            self.update_state(
                connected=True,
                ready=False,
                name=candidate_name,
                left=None,
                right=None,
                mode=None,
                error="",
            )
        if self.file_descriptor < 0 and not self.connection_timer:
            self.begin_connection()

    def properties_changed(self, interface, changed, invalidated, path=None):
        if str(interface) == DEVICE_INTERFACE:
            self.evaluate_devices()

    def interfaces_changed(self, *args):
        self.evaluate_devices()

    def begin_connection(self):
        if not self.device_path:
            return
        self.connection_attempt = 0
        self.try_connect()
        self.connection_timer = GLib.timeout_add(500, self.connection_tick)

    def connection_tick(self):
        if self.file_descriptor >= 0 or not self.device_path:
            self.connection_timer = 0
            return GLib.SOURCE_REMOVE
        self.connection_attempt += 1
        if self.connection_attempt in (1, 2, 4, 8):
            self.try_connect()
        if self.connection_attempt > 8:
            self.connection_timer = 0
            self.update_state(error="Unable to open the Robin control channel")
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def try_connect(self):
        if not self.device_path:
            return
        device = dbus.Interface(
            self.system_bus.get_object(BLUEZ_SERVICE, self.device_path),
            DEVICE_INTERFACE,
        )
        device.ConnectProfile(
            SPP_UUID,
            reply_handler=lambda: None,
            error_handler=self.connection_error,
        )

    def connection_error(self, error):
        error_name = error.get_dbus_name()
        if error_name not in ("org.bluez.Error.AlreadyConnected", "org.bluez.Error.InProgress"):
            self.update_state(error=f"Control channel: {error_name}")

    def accept_channel(self, device_path, descriptor):
        if device_path != self.device_path:
            os.close(descriptor)
            return
        self.detach_channel(False)
        self.file_descriptor = descriptor
        os.set_blocking(self.file_descriptor, True)
        self.decoder.reset()
        self.handshake_accepted = False
        self.battery_committed = False
        self.battery_attempt = 0
        self.expected_mode = ""
        self.noise_attempt = 0
        self.clear_timer("connection_timer")
        self.channel_watch = GLib.io_add_watch(
            self.file_descriptor,
            GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR | GLib.IO_NVAL,
            self.channel_event,
        )
        self.update_state(ready=False, left=None, right=None, mode=None, error="")
        self.send(HANDSHAKE)
        self.handshake_timer = GLib.timeout_add(5000, self.handshake_timeout)

    def channel_event(self, source, condition):
        if condition & (GLib.IO_HUP | GLib.IO_ERR | GLib.IO_NVAL):
            self.channel_watch = 0
            self.detach_channel(True)
            return GLib.SOURCE_REMOVE
        try:
            data = os.read(self.file_descriptor, 1024)
        except OSError:
            data = b""
        if not data:
            self.channel_watch = 0
            self.detach_channel(True)
            return GLib.SOURCE_REMOVE
        for frame in self.decoder.offer(data):
            self.handle_frame(frame)
        return GLib.SOURCE_CONTINUE

    def detach_channel(self, reconnect):
        if self.channel_watch:
            GLib.source_remove(self.channel_watch)
            self.channel_watch = 0
        if self.file_descriptor >= 0:
            try:
                os.close(self.file_descriptor)
            except OSError:
                pass
            self.file_descriptor = -1
        self.decoder.reset()
        self.handshake_accepted = False
        self.clear_timer("handshake_timer")
        self.clear_timer("poll_timer")
        self.clear_timer("battery_timer")
        self.clear_timer("noise_timer")
        if self.state["connected"]:
            self.update_state(ready=False)
        if reconnect and self.device_path and not self.connection_timer:
            GLib.timeout_add(1000, self.delayed_reconnect)

    def delayed_reconnect(self):
        if self.device_path and self.file_descriptor < 0 and not self.connection_timer:
            self.begin_connection()
        return GLib.SOURCE_REMOVE

    def handshake_timeout(self):
        self.handshake_timer = 0
        if not self.handshake_accepted:
            self.update_state(error="Robin did not accept the protocol handshake")
            self.detach_channel(True)
        return GLib.SOURCE_REMOVE

    def send(self, data):
        if self.file_descriptor < 0:
            return False
        try:
            view = memoryview(data)
            while view:
                written = os.write(self.file_descriptor, view)
                view = view[written:]
            return True
        except OSError as error:
            self.update_state(error=f"Bluetooth write failed: {error.strerror}")
            self.detach_channel(True)
            return False

    def handle_frame(self, frame):
        command, subcommand, opcode, parameters = frame
        if not self.handshake_accepted:
            if (command, subcommand, opcode, parameters) == (0x0A, 0x83, 0x00, b"\x00\x04\x03\x01"):
                self.handshake_accepted = True
                self.clear_timer("handshake_timer")
                self.update_state(ready=True, error="")
                self.refresh()
                self.poll_timer = GLib.timeout_add_seconds(30, self.poll)
            elif command == 0x0A and subcommand == 0x83:
                self.update_state(error="Robin rejected the protocol handshake")
                self.detach_channel(False)
            return
        if command == 0x1D and subcommand == 0x1B and opcode == 0x01 and len(parameters) == 4:
            if parameters[0] == 0x01 and parameters[2] == 0x02 and parameters[1] <= 100 and parameters[3] <= 100:
                self.handle_battery(parameters[1], parameters[3])
            return
        if command == 0x1D and subcommand == 0x11 and opcode == 0x03 and len(parameters) == 4:
            if parameters[1:] == b"\x01\x00\x00" and parameters[0] in NOISE_QUERY_VALUES:
                self.handle_noise_mode(NOISE_QUERY_VALUES[parameters[0]])

    def handle_battery(self, left, right):
        provisional = left == 0 or right == 0
        delays = (500, 800, 1200, 1600)
        if not self.battery_committed and provisional and self.battery_attempt < len(delays):
            delay = delays[self.battery_attempt]
            self.battery_attempt += 1
            self.clear_timer("battery_timer")
            self.battery_timer = GLib.timeout_add(delay, self.query_battery)
            return
        self.battery_committed = True
        self.clear_timer("battery_timer")
        self.update_state(left=left, right=right)

    def query_battery(self):
        self.battery_timer = 0
        self.send(QUERY_BATTERY)
        return GLib.SOURCE_REMOVE

    def handle_noise_mode(self, mode):
        if not self.expected_mode or mode == self.expected_mode:
            self.expected_mode = ""
            self.noise_attempt = 0
            self.clear_timer("noise_timer")
            self.update_state(mode=mode)
            return
        delays = (500, 700, 900, 1200)
        if self.noise_attempt < len(delays):
            delay = delays[self.noise_attempt]
            self.noise_attempt += 1
            self.clear_timer("noise_timer")
            self.noise_timer = GLib.timeout_add(delay, self.query_noise_mode)
            return
        self.expected_mode = ""
        self.noise_attempt = 0
        self.clear_timer("noise_timer")
        self.update_state(mode=mode)

    def query_noise_mode(self):
        self.noise_timer = 0
        self.send(QUERY_NOISE_MODE)
        return GLib.SOURCE_REMOVE

    def set_noise_mode(self, mode):
        if not self.handshake_accepted or mode not in NOISE_WRITE_VALUES:
            return
        self.expected_mode = mode
        self.noise_attempt = 0
        self.clear_timer("noise_timer")
        self.update_state(mode=mode)
        self.send(encode_frame(0x1D, 0x10, 0x04, bytes((NOISE_WRITE_VALUES[mode],))))
        self.noise_timer = GLib.timeout_add(600, self.query_noise_mode)

    def refresh(self):
        if not self.handshake_accepted:
            return
        self.send(QUERY_BATTERY)
        self.send(QUERY_NOISE_MODE)

    def poll(self):
        self.refresh()
        return GLib.SOURCE_CONTINUE

    def clear_timer(self, name):
        timer = getattr(self, name)
        if timer:
            GLib.source_remove(timer)
            setattr(self, name, 0)


class StateClient:
    def __init__(self, session_bus):
        self.session_bus = session_bus
        self.interface = None
        self.session_bus.add_signal_receiver(
            self.owner_changed,
            dbus_interface="org.freedesktop.DBus",
            signal_name="NameOwnerChanged",
            arg0=SESSION_BUS_NAME,
        )
        self.session_bus.add_signal_receiver(
            self.state_changed,
            dbus_interface=SESSION_INTERFACE,
            signal_name="StateChanged",
        )
        GLib.io_add_watch(sys.stdin.fileno(), GLib.IO_IN | GLib.IO_HUP, self.stdin_event)
        self.connect()

    def emit(self, state):
        print(state, flush=True)

    def disconnected_state(self):
        return json.dumps(
            {
                "connected": False,
                "ready": False,
                "name": "MOONDROP Robin",
                "left": None,
                "right": None,
                "mode": None,
                "error": "",
            },
            separators=(",", ":"),
        )

    def connect(self):
        if not self.session_bus.name_has_owner(SESSION_BUS_NAME):
            self.interface = None
            self.emit(self.disconnected_state())
            return
        proxy = self.session_bus.get_object(SESSION_BUS_NAME, SESSION_OBJECT_PATH)
        self.interface = dbus.Interface(proxy, SESSION_INTERFACE)
        try:
            self.emit(str(self.interface.GetState()))
        except dbus.DBusException:
            self.interface = None
            self.emit(self.disconnected_state())

    def owner_changed(self, name, old_owner, new_owner):
        if str(new_owner):
            self.connect()
        else:
            self.interface = None
            self.emit(self.disconnected_state())

    def state_changed(self, state):
        self.emit(str(state))

    def stdin_event(self, source, condition):
        if condition & GLib.IO_HUP:
            GLib.MainLoop().quit()
            return GLib.SOURCE_REMOVE
        line = sys.stdin.readline()
        if not line:
            return GLib.SOURCE_REMOVE
        try:
            command = json.loads(line)
        except json.JSONDecodeError:
            return GLib.SOURCE_CONTINUE
        if not self.interface:
            self.connect()
        if not self.interface:
            return GLib.SOURCE_CONTINUE
        try:
            if command.get("action") == "set_mode":
                self.interface.SetNoiseMode(str(command.get("value", "")))
            elif command.get("action") == "refresh":
                self.interface.Refresh()
        except dbus.DBusException:
            self.interface = None
        return GLib.SOURCE_CONTINUE


def run_backend():
    DBusGMainLoop(set_as_default=True)
    system_bus = dbus.SystemBus()
    session_bus = dbus.SessionBus()
    bus_name = dbus.service.BusName(SESSION_BUS_NAME, session_bus, do_not_queue=True)
    controller = RobinController(system_bus)
    api = SessionApi(bus_name, controller)
    controller.set_api(api)
    controller.start()
    loop = GLib.MainLoop()

    def stop(signum, frame):
        loop.quit()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    loop.run()
    controller.stop()


def run_client():
    DBusGMainLoop(set_as_default=True)
    StateClient(dbus.SessionBus())
    loop = GLib.MainLoop()

    def stop(signum, frame):
        loop.quit()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    loop.run()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("backend", "watch"):
        raise SystemExit("usage: dank_buds.py backend|watch")
    if sys.argv[1] == "backend":
        run_backend()
    else:
        run_client()
