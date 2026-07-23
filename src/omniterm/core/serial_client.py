from PyQt6.QtCore import QThread, pyqtSignal
from omniterm.core.threads import register
import serial
import time

class SerialWorker(QThread):
    data_received = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    disconnected = pyqtSignal(str)

    def __init__(self, port, baud_rate=115200, data_bits=8, parity='N', stop_bits=1):
        super().__init__()
        register(self, "serial-worker")
        self.port = port
        self.baud_rate = baud_rate
        self.data_bits = data_bits
        self.parity = parity
        self.stop_bits = stop_bits
        self._running = True

    def run(self):
        try:
            ser = serial.Serial(
                port=self.port,
                baudrate=self.baud_rate,
                bytesize=self.data_bits,
                parity=self.parity,
                stopbits=self.stop_bits,
                timeout=0.1
            )
            self.ser = ser
            
            buffer = ""
            last_emit_time = time.time()
            
            while self._running:
                if ser.in_waiting > 0:
                    data = ser.read(ser.in_waiting).decode('utf-8', errors='replace')
                    buffer += data
                
                # Emit buffered data every 50ms to prevent UI freezing
                current_time = time.time()
                if buffer and (current_time - last_emit_time > 0.05):
                    self.data_received.emit(buffer)
                    buffer = ""
                    last_emit_time = current_time
                
                time.sleep(0.01)
                
            try:
                ser.close()
            except Exception:
                pass
        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            if self._running:
                self.disconnected.emit("Serial connection closed.")

    def stop(self):
        self._running = False

    def send_data(self, data):
        try:
            if hasattr(self, 'ser') and self.ser:
                self.ser.write(data.encode('utf-8'))
        except Exception as e:
            self.error_occurred.emit(f"Serial Write Error: {e}")
