import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from gui import TaskAdvancerApp

def main():
    app = TaskAdvancerApp()
    app.mainloop()

if __name__ == "__main__":
    main()
