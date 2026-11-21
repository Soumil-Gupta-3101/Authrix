import logging
from threading import Lock
import time
import unicodedata


log = logging.getLogger(__name__)


def lcd_line(text):
    return unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()[:16].ljust(16)


class Feedback:
    def __init__(self, settings):
        self.settings = settings
        self.lock = Lock()
        self.enabled = settings.mode == "raspberry_pi" and settings.section("lcd").get("enabled", True)
        status = "dashboard"
        if settings.mode == "raspberry_pi":
            status = "pending" if self.enabled else "disabled"
        self.state = {"lines": [lcd_line("Attendance"), lcd_line("Ready")], "lcd_status": status, "warning": None}
        self.lcd = None
        self.retry_at = 0

    def open(self):
        if not self.enabled or self.lcd is not None or time.monotonic() < self.retry_at:
            return
        options = self.settings.section("lcd")
        try:
            from RPLCD.i2c import CharLCD
            self.lcd = CharLCD(i2c_expander=options.get("expander", "PCF8574"), address=options.get("address", 0x27),
                               port=options.get("bus", 1), cols=16, rows=2, auto_linebreaks=False)
            with self.lock:
                self.state.update(lcd_status="connected", warning=None)
        except Exception as exc:
            self._lcd_failed(exc)

    def show(self, first, second=""):
        lines = [lcd_line(first), lcd_line(second)]
        with self.lock:
            self.state["lines"] = lines
        self.open()
        if self.lcd is not None:
            try:
                for row, text in enumerate(lines):
                    self.lcd.cursor_pos = (row, 0)
                    self.lcd.write_string(text)
            except Exception as exc:
                self._lcd_failed(exc)

    def _lcd_failed(self, exc):
        self.close()
        # Avoid opening a missing I2C device on every face-capture frame.
        self.retry_at = time.monotonic() + 5
        warning = f"LCD unavailable. Using dashboard feedback. {exc}"
        with self.lock:
            previous = self.state["warning"]
            self.state.update(lcd_status="unavailable", warning=warning)
        if warning != previous:
            log.warning(warning)

    def result(self, accepted, message):
        self.show("Attendance saved" if accepted else "Not marked", message)

    def snapshot(self):
        with self.lock:
            return {**self.state, "lines": list(self.state["lines"])}

    def close(self):
        lcd, self.lcd = self.lcd, None
        if lcd is not None:
            try:
                lcd.close(clear=True)
            except Exception as exc:
                log.warning("Could not close LCD: %s", exc)
