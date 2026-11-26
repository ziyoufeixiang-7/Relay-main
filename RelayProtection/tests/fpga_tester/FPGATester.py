import os
import sys
import serial
import json
import logging
from flask import Flask, render_template, request, jsonify

# Add the parent directory to sys.path so we can import logic.FPGACodec
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
try:
    from logic.FPGACodec import HWCodec, HWConfig
except ImportError as e:
    print(f"Failed to import FPGACodec: {e}")
    sys.exit(1)

app = Flask(__name__)
logger = logging.getLogger("FPGATester")
logging.basicConfig(level=logging.INFO)

# Mock Calibration specifically for DAC direct view
class DirectDACCalibration:
    @classmethod
    def PhysToReg(cls, chIdx: int, layerIdx: int, amplitude: float, freqOrPhase: float) -> tuple[int, int]:
        """
        Convert physical values to FPGA register values.
        - amplitude: -10V to 10V
        - FPGA register high 16-bits map to -32767 to 32767
        - So amplitude * 3276.7 is the required I16 value.
        """
        # Convert amplitude: 10.0 -> 32767, then move to upper 16 bits of a 32-bit int
        # 32767 * 65536 = 2147418112
        aReg = int(round(amplitude / 10.0 * 2147418112)) & 0xFFFFFFFF
        
        if layerIdx == 0xFF: # 255 is DC layer
            # DC: p2 is frequency
            pReg = HWConfig.ConvertFreqToReg(freqOrPhase)
        else:
            # AC: p2 is phase
            pReg = HWConfig.ConvertPhaseToReg(freqOrPhase)
            
        return aReg, pReg

# Initialize Serial
try:
    ser = serial.Serial('/dev/ttyAMA0', 230400, timeout=1)
    logger.info("Successfully opened serial port /dev/ttyAMA0")
except Exception as e:
    logger.error(f"Failed to open serial port: {e}. Is another process using it?")
    ser = None

@app.route('/')
def index():
    return render_template('tester.html')

@app.route('/api/sys_cmd', methods=['POST'])
def sys_cmd():
    data = request.json
    cmd = data.get('cmd')
    frame = b''
    try:
        if cmd == 'SYS_START':
            frame = HWCodec.FRAME_SYS_START
        elif cmd == 'SYS_STOP':
            frame = HWCodec.FRAME_SYS_STOP
        elif cmd == 'SYS_RESET':
            frame = HWCodec.FRAME_SYS_RESET
        elif cmd == 'SYS_UPDATE':
            frame = HWCodec.FRAME_SYS_UPDATE
        elif cmd == 'SYS_SYNC':
            frame = HWCodec.FRAME_SYS_SYNC
        elif cmd == 'SYS_SET_DBNC':
            val = float(data.get('val', 0))
            reg = HWConfig.ConvertDbncToReg(val)
            frame = HWCodec.BuildSystemFrame(HWCodec.SYS_SET_DBNC, reg)
        elif cmd == 'SYS_SET_DO':
            val = int(data.get('val', 0))
            frame = HWCodec.BuildSystemFrame(HWCodec.SYS_SET_DO, val)
        else:
            return jsonify({'error': 'Unknown command'}), 400

        if ser:
            ser.write(frame)
            return jsonify({'status': 'ok', 'sent': frame.hex().upper()})
        return jsonify({'error': 'Serial port not open', 'sent': frame.hex().upper()}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/param_cmd', methods=['POST'])
def param_cmd():
    data = request.json
    cmd_str = data.get('cmd')
    cmd_map = {
        'DDS_WR_SHADOW': HWCodec.DDS_WR_SHADOW,
        'DDS_WR_STAGE': HWCodec.DDS_WR_STAGE,
        'DDS_STEP_SHADOW': HWCodec.DDS_STEP_SHADOW,
        'DDS_STEP_STAGE': HWCodec.DDS_STEP_STAGE
    }
    cmd_code = cmd_map.get(cmd_str)
    
    if cmd_code is None:
        return jsonify({'error': 'Unknown command'}), 400
        
    try:
        regIndex = int(data.get('regIndex', 0))
        chMask = int(data.get('chMask', 0))
        amplitude = float(data.get('amplitude', 0))
        freqOrPhase = float(data.get('freqOrPhase', 0))
        
        p1, p2 = DirectDACCalibration.PhysToReg(0, regIndex, amplitude, freqOrPhase)
        
        frame = HWCodec.BuildParamFrame(cmd_code, regIndex, chMask, p1, p2)
        if ser:
            ser.write(frame)
            return jsonify({'status': 'ok', 'sent': frame.hex().upper()})
        return jsonify({'error': 'Serial port not open', 'sent': frame.hex().upper()}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/phase_gate', methods=['POST'])
def phase_gate():
    data = request.json
    try:
        physical_ch = int(data.get('physical_ch', 0))
        phase = float(data.get('phase', 0))
        
        p2 = HWConfig.ConvertPhaseToReg(phase)
        frame = HWCodec.BuildPhaseGateFrame(physical_ch, p2)
        
        if ser:
            ser.write(frame)
            return jsonify({'status': 'ok', 'sent': frame.hex().upper()})
        return jsonify({'error': 'Serial port not open', 'sent': frame.hex().upper()}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("Starting FPGA Tester on port 5000...")
    app.run(host='0.0.0.0', port=5000, debug=True)
