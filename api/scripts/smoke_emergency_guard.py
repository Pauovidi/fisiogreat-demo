import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.utils.emergency_guard import detect_emergency, emergency_reply


def main():
    text = "dolor en el pecho y no puedo respirar"
    detection = detect_emergency(text)
    print(f"DETECTED: {detection.detected}")
    print(f"MATCHED: {detection.matched}")
    print(emergency_reply("whatsapp"))


if __name__ == "__main__":
    main()
