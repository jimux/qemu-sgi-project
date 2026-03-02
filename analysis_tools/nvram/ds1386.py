"""Dallas DS1386 RAMified Watchdog Timekeeper decoder.

The DS1386 is used in SGI Indy (IP22/IP24) systems. It provides:
- Real-time clock with hundredths-of-second resolution
- Battery-backed RAM (8KB for DS1386-8K)
- Watchdog timer
- Time-of-day alarm

MAME saves this as an 8KB file containing the complete RAM contents.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any

from ..utils.endian import bcd_to_int, read_cstring


@dataclass
class DS1386Time:
    """Decoded time from DS1386 RTC."""
    hundredths: int
    seconds: int
    minutes: int
    hours: int
    day_of_week: int
    date: int
    month: int
    year: int
    is_24hour: bool
    is_pm: bool  # Only valid in 12-hour mode

    def __str__(self) -> str:
        if self.is_24hour:
            time_str = f"{self.hours:02d}:{self.minutes:02d}:{self.seconds:02d}.{self.hundredths:02d}"
        else:
            ampm = "PM" if self.is_pm else "AM"
            time_str = f"{self.hours:02d}:{self.minutes:02d}:{self.seconds:02d}.{self.hundredths:02d} {ampm}"

        # Assume 1900s/2000s based on year value
        full_year = 2000 + self.year if self.year < 70 else 1900 + self.year

        # Handle potentially invalid date values
        date_val = self.date if 1 <= self.date <= 31 else f"?{self.date}"
        month_val = self.month if 1 <= self.month <= 12 else f"?{self.month}"
        date_str = f"{full_year:04d}-{month_val:02d}-{date_val:02d}" if isinstance(month_val, int) and isinstance(date_val, int) else f"{full_year:04d}-{month_val}-{date_val}"

        days = ["???", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        dow = days[self.day_of_week] if 0 <= self.day_of_week <= 7 else "???"

        return f"{date_str} ({dow}) {time_str}"

    def is_valid(self) -> bool:
        """Check if the time/date values are all within valid ranges."""
        return (
            0 <= self.hundredths <= 99 and
            0 <= self.seconds <= 59 and
            0 <= self.minutes <= 59 and
            0 <= self.hours <= 23 and
            1 <= self.day_of_week <= 7 and
            1 <= self.date <= 31 and
            1 <= self.month <= 12 and
            0 <= self.year <= 99
        )


class DS1386:
    """Dallas DS1386 RTC decoder for SGI systems.

    Register map (addresses 0x00-0x0D):
        0x00: Hundredths (BCD 00-99)
        0x01: Seconds (BCD 00-59, bit 7 = oscillator disable)
        0x02: Minutes (BCD 00-59)
        0x03: Minutes Alarm
        0x04: Hours (BCD, bit 6 = 12/24, bit 5 = AM/PM in 12h mode)
        0x05: Hours Alarm
        0x06: Day of Week (1-7, Sunday=1)
        0x07: Day Alarm
        0x08: Date (BCD 01-31)
        0x09: Month (BCD 01-12, upper bits = enables/outputs)
        0x0A: Year (BCD 00-99)
        0x0B: Command register
        0x0C: Watchdog Hundredths
        0x0D: Watchdog Seconds
        0x0E+: User RAM
    """

    # Register offsets
    REG_HUNDREDTHS = 0x00
    REG_SECONDS = 0x01
    REG_MINUTES = 0x02
    REG_MINUTES_ALARM = 0x03
    REG_HOURS = 0x04
    REG_HOURS_ALARM = 0x05
    REG_DAY_OF_WEEK = 0x06
    REG_DAY_ALARM = 0x07
    REG_DATE = 0x08
    REG_MONTH = 0x09
    REG_YEAR = 0x0A
    REG_COMMAND = 0x0B
    REG_WATCHDOG_HUNDREDTHS = 0x0C
    REG_WATCHDOG_SECONDS = 0x0D
    REG_USER_RAM = 0x0E

    # Bit masks
    SECONDS_OSC_DISABLE = 0x80
    HOURS_12_24 = 0x40  # 0 = 24-hour, 1 = 12-hour
    HOURS_AM_PM = 0x20  # In 12-hour mode: 0 = AM, 1 = PM

    # Command register bits
    CMD_TE = 0x80      # Transfer Enable
    CMD_IPSW = 0x40    # Interrupt Polarity / SQW
    CMD_IBH_LO = 0x20
    CMD_PU_LVL = 0x10
    CMD_WAM = 0x08     # Watchdog Alarm Mask
    CMD_TDM = 0x04     # Time-of-Day Alarm Mask
    CMD_WAF = 0x02     # Watchdog Alarm Flag
    CMD_TDF = 0x01     # Time-of-Day Alarm Flag

    # MAME file size for DS1386-8K
    EXPECTED_SIZE_8K = 8192

    def __init__(self, data: bytes):
        """Initialize decoder with raw NVRAM data."""
        self.data = data
        self.size = len(data)

    @classmethod
    def from_file(cls, filepath: str) -> 'DS1386':
        """Load DS1386 data from a file."""
        with open(filepath, 'rb') as f:
            data = f.read()
        return cls(data)

    def get_time(self) -> DS1386Time:
        """Decode the current RTC time/date."""
        hundredths = bcd_to_int(self.data[self.REG_HUNDREDTHS])
        seconds = bcd_to_int(self.data[self.REG_SECONDS] & 0x7F)
        minutes = bcd_to_int(self.data[self.REG_MINUTES])

        hours_raw = self.data[self.REG_HOURS]
        is_24hour = (hours_raw & self.HOURS_12_24) == 0
        is_pm = False

        if is_24hour:
            hours = bcd_to_int(hours_raw & 0x3F)
        else:
            is_pm = (hours_raw & self.HOURS_AM_PM) != 0
            hours = bcd_to_int(hours_raw & 0x1F)

        day_of_week = self.data[self.REG_DAY_OF_WEEK] & 0x07
        date = bcd_to_int(self.data[self.REG_DATE])
        month = bcd_to_int(self.data[self.REG_MONTH] & 0x1F)
        year = bcd_to_int(self.data[self.REG_YEAR])

        return DS1386Time(
            hundredths=hundredths,
            seconds=seconds,
            minutes=minutes,
            hours=hours,
            day_of_week=day_of_week,
            date=date,
            month=month,
            year=year,
            is_24hour=is_24hour,
            is_pm=is_pm
        )

    def get_command_register(self) -> Dict[str, bool]:
        """Decode the command register flags."""
        cmd = self.data[self.REG_COMMAND]
        return {
            'transfer_enable': bool(cmd & self.CMD_TE),
            'ipsw': bool(cmd & self.CMD_IPSW),
            'ibh_lo': bool(cmd & self.CMD_IBH_LO),
            'pu_lvl': bool(cmd & self.CMD_PU_LVL),
            'watchdog_alarm_mask': bool(cmd & self.CMD_WAM),
            'tod_alarm_mask': bool(cmd & self.CMD_TDM),
            'watchdog_alarm_flag': bool(cmd & self.CMD_WAF),
            'tod_alarm_flag': bool(cmd & self.CMD_TDF),
        }

    def is_oscillator_disabled(self) -> bool:
        """Check if the oscillator is disabled."""
        return bool(self.data[self.REG_SECONDS] & self.SECONDS_OSC_DISABLE)

    def get_user_ram(self) -> bytes:
        """Get the user RAM portion (everything after register 0x0E)."""
        return self.data[self.REG_USER_RAM:]

    def get_user_ram_size(self) -> int:
        """Get the size of user RAM."""
        return self.size - self.REG_USER_RAM

    def analyze(self) -> Dict[str, Any]:
        """Perform complete analysis of the DS1386 data."""
        return {
            'time': self.get_time(),
            'oscillator_disabled': self.is_oscillator_disabled(),
            'command': self.get_command_register(),
            'user_ram_size': self.get_user_ram_size(),
            'total_size': self.size,
        }

    def format_report(self) -> str:
        """Generate a human-readable report."""
        lines = []
        lines.append("=== DS1386 RTC Analysis ===")
        lines.append("")

        time = self.get_time()
        lines.append(f"Time/Date: {time}")
        if not time.is_valid():
            lines.append("  (Warning: Some RTC values are outside normal range)")
        lines.append(f"Mode: {'24-hour' if time.is_24hour else '12-hour'}")
        lines.append(f"Oscillator: {'DISABLED' if self.is_oscillator_disabled() else 'Running'}")
        lines.append("")

        lines.append("Command Register:")
        cmd = self.get_command_register()
        for key, value in cmd.items():
            lines.append(f"  {key}: {value}")
        lines.append("")

        lines.append(f"User RAM: {self.get_user_ram_size()} bytes")
        lines.append(f"Total Size: {self.size} bytes")

        return "\n".join(lines)
