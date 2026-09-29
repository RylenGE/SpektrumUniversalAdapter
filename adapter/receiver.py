import threading
import time
from collections import deque

import serial

from .protocol import BridgeParser


class SerialReceiver(threading.Thread):
    def __init__(self, port, baud, event_bus=None, on_disconnect=None):
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.event_bus = event_bus
        self.on_disconnect = on_disconnect

        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.parser = BridgeParser()
        self.ser = None

        self.latest = None
        self.latest_channels = {}
        self.history = deque(maxlen=1200)

        self.valid_packets = 0
        self.sequence_missing = 0
        self.last_sequence = None

        self.rate_count = 0
        self.packet_rate = 0.0
        self.rate_started = time.monotonic()

    def stop(self):
        self.stop_event.set()
        try:
            if self.ser is not None:
                self.ser.close()
        except Exception:
            pass

    def snapshot(self):
        with self.lock:
            if self.latest is None:
                return None
            out = dict(self.latest)
            out["channels"] = dict(self.latest_channels)
            return out

    def channels(self):
        with self.lock:
            return dict(self.latest_channels)

    def history_since(self, started_at):
        with self.lock:
            return [
                {
                    "time": item["time"],
                    "channels": dict(item["channels"]),
                }
                for item in self.history
                if item["time"] >= started_at
            ]

    def stats(self):
        with self.lock:
            return {
                "valid": self.valid_packets,
                "bad": self.parser.bad_packets,
                "missing": self.sequence_missing,
                "rate": self.packet_rate,
            }

    def run(self):
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.05)
            time.sleep(0.8)
            self.ser.reset_input_buffer()

            while not self.stop_event.is_set():
                try:
                    waiting = self.ser.in_waiting
                    data = self.ser.read(waiting if waiting else 1)
                except Exception:
                    break

                for packet in self.parser.feed(data):
                    self._handle_packet(packet)

                now = time.monotonic()
                elapsed = now - self.rate_started
                if elapsed >= 1.0:
                    with self.lock:
                        self.packet_rate = self.rate_count / elapsed
                    self.rate_count = 0
                    self.rate_started = now
        finally:
            try:
                if self.ser is not None:
                    self.ser.close()
            except Exception:
                pass

            if self.on_disconnect and not self.stop_event.is_set():
                try:
                    self.on_disconnect()
                except Exception:
                    pass

    def _handle_packet(self, packet):
        sequence = packet["sequence"]

        if self.last_sequence is not None:
            expected = (self.last_sequence + 1) & 0xFF
            if sequence != expected:
                self.sequence_missing += (sequence - expected) & 0xFF
        self.last_sequence = sequence

        now = time.monotonic()

        with self.lock:
            self.latest_channels.update(packet["channels"])
            item = {
                **packet,
                "time": now,
                "channels": dict(self.latest_channels),
            }
            self.latest = item
            self.history.append(item)
            self.valid_packets += 1
            self.rate_count += 1

        if self.event_bus:
            self.event_bus.emit("receiver.packet", packet=self.snapshot())
            self.event_bus.emit("channels.changed", channels=self.channels())
