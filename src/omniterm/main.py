import sys
from PyQt6.QtWidgets import QApplication
from omniterm.ui.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("OmniTerm")
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
