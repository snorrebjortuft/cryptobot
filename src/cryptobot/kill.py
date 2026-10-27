"""Kill switch: stop the bot at its next check (within about a minute).

    python -m cryptobot.kill           # stop, keep coins
    python -m cryptobot.kill --sell    # stop and sell everything to EUR

Creating a file named STOP in the project folder by hand does the same.
To start again after reviewing: delete STOP and run the bot with --resume.
"""

import argparse

from cryptobot.risk import KILL_FILE


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--sell", action="store_true", help="also sell all coins")
    args = parser.parse_args()
    KILL_FILE.write_text("sell\n" if args.sell else "stop\n", encoding="utf-8")
    print(f"Kill switch set ({'stop and sell' if args.sell else 'stop'}): {KILL_FILE.resolve()}")


if __name__ == "__main__":
    main()
