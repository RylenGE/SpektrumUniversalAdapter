HEADER = b"\xA5\x5A"
V1_PACKET_SIZE = 21


def crc8(data):
    crc = 0
    for value in data:
        crc ^= value
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


class BridgeParser:
    """
    Parses both:
      v1: old fixed CH1..CH6 bridge (21 bytes)
      v2: dynamic channel-mask bridge

    Returned packet:
      {
        "version": int,
        "sequence": int,
        "failsafe": bool,
        "rssi": int,
        "frame_loss": int,
        "channels": {1: value, 2: value, ...}
      }
    """

    def __init__(self):
        self.buffer = bytearray()
        self.bad_packets = 0

    def feed(self, data):
        if data:
            self.buffer.extend(data)

        packets = []

        while True:
            pos = self.buffer.find(HEADER)
            if pos < 0:
                if len(self.buffer) > 1:
                    del self.buffer[:-1]
                break

            if pos > 0:
                del self.buffer[:pos]

            if len(self.buffer) < 4:
                break

            version = self.buffer[2]

            if version == 1:
                needed = V1_PACKET_SIZE
            elif version == 2:
                needed = self.buffer[3]
                if needed < 14 or needed > 96:
                    self.bad_packets += 1
                    del self.buffer[0]
                    continue
            else:
                self.bad_packets += 1
                del self.buffer[0]
                continue

            if len(self.buffer) < needed:
                break

            raw = bytes(self.buffer[:needed])

            if version == 1:
                valid = crc8(raw[2:20]) == raw[20]
            else:
                valid = crc8(raw[2:-1]) == raw[-1]

            if not valid:
                self.bad_packets += 1
                del self.buffer[0]
                continue

            del self.buffer[:needed]

            if version == 1:
                packet = self._parse_v1(raw)
            else:
                packet = self._parse_v2(raw)

            if packet is None:
                self.bad_packets += 1
            else:
                packets.append(packet)

        return packets

    @staticmethod
    def _signed_byte(value):
        return value if value < 128 else value - 256

    def _parse_v1(self, raw):
        channels = {}
        offset = 8
        for channel in range(1, 7):
            channels[channel] = raw[offset] | (raw[offset + 1] << 8)
            offset += 2

        return {
            "version": 1,
            "sequence": raw[3],
            "failsafe": bool(raw[4] & 0x01),
            "rssi": self._signed_byte(raw[5]),
            "frame_loss": raw[6] | (raw[7] << 8),
            "channels": channels,
        }

    def _parse_v2(self, raw):
        # 0..1 header
        # 2 version
        # 3 total length
        # 4 seq
        # 5 flags
        # 6 RSSI
        # 7..8 frame losses
        # 9..12 32-bit channel mask
        # 13.. values
        if len(raw) < 14:
            return None

        mask = (
            raw[9]
            | (raw[10] << 8)
            | (raw[11] << 16)
            | (raw[12] << 24)
        )

        channels = {}
        offset = 13

        for bit in range(32):
            if mask & (1 << bit):
                if offset + 1 >= len(raw) - 1:
                    return None
                channels[bit + 1] = raw[offset] | (raw[offset + 1] << 8)
                offset += 2

        if offset != len(raw) - 1:
            return None

        return {
            "version": 2,
            "sequence": raw[4],
            "failsafe": bool(raw[5] & 0x01),
            "rssi": self._signed_byte(raw[6]),
            "frame_loss": raw[7] | (raw[8] << 8),
            "channels": channels,
        }
