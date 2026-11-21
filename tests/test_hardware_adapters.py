from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from attendance.config import Settings
from attendance.devices.camera import Camera
from attendance.devices.feedback import Feedback


class HardwareAdapterTests(unittest.TestCase):
    def test_connected_lcd_writes_both_lines_without_gpio(self):
        lcd_constructor = MagicMock()
        modules = {"RPLCD": SimpleNamespace(), "RPLCD.i2c": SimpleNamespace(CharLCD=lcd_constructor),
                   "gpiozero": None}
        settings = Settings("raspberry_pi", Path("."), "Asia/Kolkata", {"lcd": {"address": 0x3F}})
        with patch.dict("sys.modules", modules):
            feedback = Feedback(settings)
            feedback.show("Test", "12345678901234567890")
            lcd_constructor.assert_called_once_with(i2c_expander="PCF8574", address=0x3F, port=1, cols=16, rows=2, auto_linebreaks=False)
            lcd = feedback.lcd
            self.assertEqual([call.args[0] for call in lcd.write_string.call_args_list],
                             ["Test            ", "1234567890123456"])
            self.assertEqual(lcd.cursor_pos, (1, 0))
            self.assertEqual(feedback.snapshot()["lcd_status"], "connected")
            feedback.close()
            lcd.close.assert_called_once_with(clear=True)

    def test_missing_lcd_keeps_prompts_and_retries_when_available(self):
        lcd = MagicMock()
        constructor = MagicMock(side_effect=[OSError("I2C device absent"), lcd])
        modules = {"RPLCD": SimpleNamespace(), "RPLCD.i2c": SimpleNamespace(CharLCD=constructor)}
        with patch.dict("sys.modules", modules), patch("attendance.devices.feedback.time.monotonic", return_value=0) as clock:
            feedback = Feedback(Settings("raspberry_pi", Path("."), "Asia/Kolkata", {}))
            self.addCleanup(feedback.close)
            feedback.show("Look at camera", "Face + finger")
            self.assertEqual(feedback.snapshot()["lcd_status"], "unavailable")
            self.assertEqual(feedback.snapshot()["lines"][0].strip(), "Look at camera")
            self.assertIn("I2C device absent", feedback.snapshot()["warning"])
            clock.return_value = 1
            feedback.result(True, "PI001")
            self.assertEqual(feedback.snapshot()["lines"][0].strip(), "Attendance saved")
            self.assertEqual(constructor.call_count, 1)
            clock.return_value = 6
            feedback.show("Place finger", "PI001")
            self.assertEqual(constructor.call_count, 2)
            self.assertEqual(feedback.snapshot()["lcd_status"], "connected")
            self.assertIsNone(feedback.snapshot()["warning"])
            self.assertEqual(lcd.write_string.call_args_list[0].args[0].strip(), "Place finger")

    def test_lcd_write_and_cleanup_failures_keep_result_on_dashboard(self):
        lcd = MagicMock()
        lcd.write_string.side_effect = OSError("LCD disconnected")
        lcd.close.side_effect = OSError("I2C close failed")
        modules = {"RPLCD": SimpleNamespace(), "RPLCD.i2c": SimpleNamespace(CharLCD=MagicMock(return_value=lcd))}
        with patch.dict("sys.modules", modules):
            feedback = Feedback(Settings("raspberry_pi", Path("."), "Asia/Kolkata", {}))
            feedback.result(False, "Identity differs")
            self.assertEqual(feedback.snapshot()["lines"], ["Not marked      ", "Identity differs"])
            self.assertEqual(feedback.snapshot()["lcd_status"], "unavailable")
            self.assertIn("LCD disconnected", feedback.snapshot()["warning"])
            self.assertIsNone(feedback.lcd)
            lcd.close.assert_called_once_with(clear=True)
            feedback.close()

    def test_missing_lcd_library_is_optional(self):
        with patch.dict("sys.modules", {"RPLCD": None, "RPLCD.i2c": None, "gpiozero": None}):
            feedback = Feedback(Settings("raspberry_pi", Path("."), "Asia/Kolkata", {}))
            feedback.result(True, "PI001")
            self.assertEqual(feedback.snapshot()["lines"][0], "Attendance saved")
            self.assertEqual(feedback.snapshot()["lcd_status"], "unavailable")

    def test_disabled_lcd_never_opens_hardware(self):
        constructor = MagicMock()
        modules = {"RPLCD": SimpleNamespace(), "RPLCD.i2c": SimpleNamespace(CharLCD=constructor)}
        settings = Settings("raspberry_pi", Path("."), "Asia/Kolkata", {"lcd": {"enabled": False}})
        with patch.dict("sys.modules", modules):
            feedback = Feedback(settings)
            feedback.result(True, "PI001")
            constructor.assert_not_called()
            self.assertEqual(feedback.snapshot()["lcd_status"], "disabled")
            self.assertIsNone(feedback.snapshot()["warning"])

    def test_camera_uses_picamera2_bgr_compatible_format_and_autofocus(self):
        camera_device = MagicMock()
        camera_device.camera_controls = {"AfMode": 0}
        constructor = MagicMock(return_value=camera_device)
        modules = {"picamera2": SimpleNamespace(Picamera2=constructor),
                   "libcamera": SimpleNamespace(controls=SimpleNamespace(AfModeEnum=SimpleNamespace(Continuous=2)))}
        with patch.dict("sys.modules", modules), patch("attendance.devices.camera.time.sleep"):
            camera = Camera("raspberry_pi", {})
            camera.open()
            camera_device.create_preview_configuration.assert_called_once_with(main={"size":(640,480),"format":"RGB888"})
            camera_device.set_controls.assert_called_once_with({"AfMode":2})
            camera.close()
            camera_device.stop.assert_called_once()
            camera_device.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
