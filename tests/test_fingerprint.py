import struct
from threading import Event
import time
import unittest
from unittest.mock import MagicMock

from attendance.devices.common import DeviceError
from attendance.devices.fingerprint import R307


class UART:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.buffer = bytearray()
        self.written = []
    def reset_input_buffer(self):
        self.buffer.clear()
    def write(self, packet):
        self.written.append(packet)
        self.buffer.extend(next(self.responses))
        return len(packet)
    def read(self, count):
        # Fragmented UART reads must also decode correctly.
        count = min(2, count)
        part = bytes(self.buffer[:count])
        del self.buffer[:count]
        return part
    def close(self):
        pass


def ack(code=0, payload=b""):
    return R307.packet(0xFFFFFFFF, bytes([code]) + payload, kind=7)


class FingerprintTests(unittest.TestCase):
    def test_known_verify_password_packet(self):
        packet = R307.packet(0xFFFFFFFF, b"\x13\x00\x00\x00\x00")
        self.assertEqual(packet.hex(), "ef01ffffffff0100071300000000001b")

    def test_handshake_reads_capacity(self):
        params = struct.pack(">HHHHIHH", 0, 9, 1000, 3, 0xFFFFFFFF, 2, 6)
        driver = R307({}, UART([ack(), ack(payload=params)]))
        driver.open()
        self.assertEqual(driver.capacity, 1000)

    def test_partial_reads_and_valid_search(self):
        driver = R307({}, UART([ack(payload=struct.pack(">HH", 8, 120))]))
        driver.capacity = 1000
        self.assertEqual(driver.search(), (8, 120))

    def test_unknown_finger(self):
        driver = R307({}, UART([ack(9)]))
        driver.capacity = 1000
        self.assertIsNone(driver.search())

    def test_corrupt_checksum_rejected(self):
        bad = bytearray(ack())
        bad[-1] ^= 1
        with self.assertRaises(DeviceError):
            R307({}, UART([bad])).command(1)

    def test_wrong_address_rejected(self):
        with self.assertRaises(DeviceError):
            R307({}, UART([R307.packet(123, b"\x00", 7)])).command(1)

    def test_short_response_times_out(self):
        with self.assertRaises(DeviceError):
            R307({"serial_timeout": .01}, UART([ack()[:6]])).command(1)

    def test_template_bitmap_uses_low_bit_first_and_caps_capacity(self):
        driver = R307({}, UART([ack(payload=b"\x05" + bytes(31))]))
        driver.capacity = 10
        self.assertEqual(driver.occupied(), {0, 2})

    def test_enrollment_selects_free_slot_and_requires_two_images(self):
        responses = [ack(payload=b"\x01" + bytes(31)), ack(2), ack(), ack(), ack(9), ack(2), ack(), ack(), ack(), ack()]
        uart = UART(responses)
        driver = R307({}, uart)
        driver.capacity = 8
        slot = driver.enroll([1], Event(), lambda _: None)
        self.assertEqual(slot, 2)
        self.assertEqual(uart.written[-1][9:-2], b"\x06\x01\x00\x02")
        self.assertIn(b"\x02\x02", [p[9:-2] for p in uart.written])

    def test_enrollment_does_not_overwrite_existing_finger(self):
        responses = [ack(payload=bytes(32)), ack(2), ack(), ack(), ack(payload=struct.pack(">HH", 3, 100))]
        uart = UART(responses)
        driver = R307({}, uart)
        driver.capacity = 8
        with self.assertRaises(DeviceError):
            driver.enroll([], Event(), lambda _: None)
        self.assertNotIn(6, [p[9] for p in uart.written])

    def test_low_score_rejected_after_fresh_finger_placement(self):
        driver = R307({}, UART([ack(2), ack(), ack(), ack(payload=struct.pack(">HH", 3, 20))]))
        driver.capacity = 8
        self.assertIsNone(driver.identify(time.monotonic() + 2, Event(), lambda _: None))

    def test_reenrollment_of_own_finger_uses_a_spare_slot(self):
        responses = [ack(payload=b"\x01" + bytes(31)), ack(2), ack(), ack(),
                     ack(payload=struct.pack(">HH", 0, 100)), ack(2), ack(), ack(), ack(), ack()]
        uart = UART(responses)
        driver = R307({}, uart)
        driver.capacity = 8
        planned = []

        def before_store(slot):
            self.assertNotIn(6, [packet[9] for packet in uart.written])
            planned.append(slot)

        slot = driver.enroll([0, 2], Event(), lambda _: None, replace_slot=0, before_store=before_store)
        self.assertEqual(slot, 1)
        self.assertEqual(planned, [1])
        self.assertEqual(uart.written[-1][9:-2], b"\x06\x01\x00\x01")
        self.assertNotIn(0x0C, [packet[9] for packet in uart.written])

    def test_reenrollment_rejects_a_different_students_finger(self):
        responses = [ack(payload=b"\x09" + bytes(31)), ack(2), ack(), ack(),
                     ack(payload=struct.pack(">HH", 3, 100))]
        uart = UART(responses)
        driver = R307({}, uart)
        driver.capacity = 8
        with self.assertRaises(DeviceError):
            driver.enroll([0, 3], Event(), lambda _: None, replace_slot=0)
        self.assertNotIn(6, [packet[9] for packet in uart.written])
        self.assertNotIn(0x0C, [packet[9] for packet in uart.written])

    def test_reenrollment_with_full_storage_preserves_existing_template(self):
        uart = UART([ack(payload=b"\xff" + bytes(31))])
        driver = R307({}, uart)
        driver.capacity = 8
        with self.assertRaisesRegex(DeviceError, "no free"):
            driver.enroll([0], Event(), lambda _: None, replace_slot=0)
        self.assertEqual([packet[9] for packet in uart.written], [0x1F])

    def test_reenrollment_capture_failure_does_not_store_or_delete(self):
        responses = [ack(payload=b"\x01" + bytes(31)), ack(2), ack(), ack(),
                     ack(9), ack(2), ack(), ack(6)]
        uart = UART(responses)
        driver = R307({}, uart)
        driver.capacity = 8
        before_store = MagicMock()
        with self.assertRaises(DeviceError):
            driver.enroll([0], Event(), lambda _: None, replace_slot=0, before_store=before_store)
        before_store.assert_not_called()
        self.assertNotIn(6, [packet[9] for packet in uart.written])
        self.assertNotIn(0x0C, [packet[9] for packet in uart.written])


if __name__ == "__main__":
    unittest.main()
