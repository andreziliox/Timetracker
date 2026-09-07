import time
import board
import busio
from adafruit_pn532.i2c import PN532_I2C

i2c = busio.I2C(board.SCL, board.SDA)
pn532 = PN532_I2C(i2c, debug=False)

ic, ver, rev, support = pn532.firmware_version
print(f"PN532 found: IC=0x{ic:02X}, firmware={ver}.{rev}")

pn532.SAM_configuration()
print("NFC ready. Hold card or tag to reader. Press Ctrl+C to stop.")

last_uid = None
last_seen = 0

while True:
    uid = pn532.read_passive_target(timeout=0.5)

    if uid is not None:
        uid_hex = uid.hex().upper()
        now = time.monotonic()

        if uid_hex != last_uid or now - last_seen > 2:
            print(f"UID: {uid_hex}")
            last_uid = uid_hex
            last_seen = now

    time.sleep(0.1)