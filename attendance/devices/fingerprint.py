"""R307/R30x EF01 UART packets, based on the GROW R307 module manual.

No GPIO voltage assumptions are made here. See docs/RASPBERRY_PI_SETUP.md.
All reads have a deadline; corrupt or unexpected ACKs fail closed.
"""
import struct
import time

from .common import DeviceError, check_deadline


class R307:
    def __init__(self, options, transport=None):
        self.options = options
        self.address = options.get("address", 0xFFFFFFFF)
        self.transport = transport
        self.capacity = None

    def open(self):
        if self.transport is None:
            import serial
            self.transport = serial.Serial(self.options.get("port", "/dev/serial0"),
                                           self.options.get("baud", 57600), timeout=.1,
                                           write_timeout=self.options.get("serial_timeout", 1))
        if self.capacity is None:
            self._ok(0x13, struct.pack(">I", self.options.get("password", 0)))
            params = self._ok(0x0F)
            if len(params) != 16:
                raise DeviceError("Unexpected R307 system parameter response")
            self.capacity = struct.unpack(">H", params[4:6])[0]
            if not 1 <= self.capacity <= 4096:
                raise DeviceError("Unsupported fingerprint storage capacity")

    @staticmethod
    def packet(address, content, kind=1):
        body = bytes([kind]) + struct.pack(">H", len(content) + 2) + content
        return b"\xef\x01" + struct.pack(">I", address) + body + struct.pack(">H", sum(body) & 0xFFFF)

    def _read(self, count, deadline):
        data = bytearray()
        while len(data) < count:
            if time.monotonic() >= deadline:
                raise DeviceError("Fingerprint UART timeout: check power, crossed TX/RX, baud and serial console")
            part = self.transport.read(count - len(data))
            if part:
                data.extend(part)
        return bytes(data)

    def command(self, instruction, payload=b""):
        if self.transport is None:
            raise DeviceError("Fingerprint port is not open")
        self.transport.reset_input_buffer()
        packet = self.packet(self.address, bytes([instruction]) + payload)
        if self.transport.write(packet) != len(packet):
            raise DeviceError("Incomplete fingerprint UART write")
        deadline = time.monotonic() + self.options.get("serial_timeout", 1)
        header = self._read(9, deadline)
        if header[:6] != b"\xef\x01" + struct.pack(">I", self.address) or header[6] != 7:
            raise DeviceError("Invalid fingerprint ACK header or module address")
        length = struct.unpack(">H", header[7:9])[0]
        if not 3 <= length <= 256:
            raise DeviceError("Invalid fingerprint ACK length")
        response = self._read(length, deadline)
        checksum = struct.unpack(">H", response[-2:])[0]
        if checksum != (sum(header[6:]) + sum(response[:-2])) & 0xFFFF:
            raise DeviceError("Fingerprint ACK checksum mismatch")
        return response[0], response[1:-2]

    def _ok(self, instruction, payload=b""):
        code, data = self.command(instruction, payload)
        if code != 0:
            raise DeviceError(f"Fingerprint command 0x{instruction:02x} rejected (code 0x{code:02x})")
        return data

    def image_present(self):
        code, _ = self.command(0x01)
        if code == 0x02:
            return False
        if code != 0:
            raise DeviceError(f"Fingerprint image failed (code 0x{code:02x}); clean sensor and retry")
        return True

    def _wait_finger(self, present, deadline, cancel):
        while True:
            check_deadline(deadline, cancel)
            if self.image_present() == present:
                check_deadline(deadline, cancel)
                return
            time.sleep(.1)

    def search(self):
        code, data = self.command(0x04, b"\x01" + struct.pack(">HH", 0, self.capacity))
        if code == 0x09:
            return None
        if code != 0 or len(data) != 4:
            raise DeviceError(f"Fingerprint search failed (code 0x{code:02x})")
        slot, score = struct.unpack(">HH", data)
        if slot >= self.capacity:
            raise DeviceError("Fingerprint sensor returned an invalid template ID")
        return slot, score

    def occupied(self):
        self.open()
        slots = set()
        for page in range((self.capacity + 255) // 256):
            bitmap = self._ok(0x1F, bytes([page]))
            if len(bitmap) != 32:
                raise DeviceError("Sensor must support the R307 template index command")
            for index, byte in enumerate(bitmap):
                for bit in range(8):
                    slot = page * 256 + index * 8 + bit
                    if byte & (1 << bit) and slot < self.capacity:
                        slots.add(slot)
        return slots

    def identify(self, deadline, cancel, progress):
        self.open()
        progress("Remove any finger")
        self._wait_finger(False, deadline, cancel)
        progress("Place your finger")
        self._wait_finger(True, deadline, cancel)
        self._ok(0x02, b"\x01")
        match = self.search()
        check_deadline(deadline, cancel)
        return match[0] if match and match[1] >= self.options.get("minimum_score", 50) else None

    def enroll(self, reserved_slots, cancel, progress, *, replace_slot=None, before_store=None):
        self.open()
        free = sorted(set(range(self.capacity)) - self.occupied() - set(reserved_slots))
        if not free:
            raise DeviceError("Fingerprint sensor is full; no free unreserved template slot")
        deadline = time.monotonic() + 60
        progress("Remove any finger")
        self._wait_finger(False, deadline, cancel)
        progress("Place finger: sample 1")
        self._wait_finger(True, deadline, cancel)
        self._ok(0x02, b"\x01")
        match = self.search()
        if match is not None and match[0] != replace_slot:
            raise DeviceError("This finger is already stored in the sensor; enrollment cancelled")
        progress("Remove your finger")
        self._wait_finger(False, deadline, cancel)
        progress("Same finger: sample 2")
        self._wait_finger(True, deadline, cancel)
        self._ok(0x02, b"\x02")
        self._ok(0x05) # RegModel verifies that both samples agree.
        check_deadline(deadline, cancel)
        slot = free[0]
        if before_store is not None:
            before_store(slot)
        self._ok(0x06, b"\x01" + struct.pack(">H", slot))
        return slot

    def delete(self, slot):
        self.open()
        self._ok(0x0C, struct.pack(">HH", slot, 1))

    def close(self):
        if self.transport is not None:
            self.transport.close()
            self.transport = None
        self.capacity = None
