if __name__ == "__main__":
    import sys
    import os
    import asyncio
    
    try:
        import gradio as gr
    except ImportError:
        print("[System] Missing Gradio. Run: pip install gradio")
        sys.exit(1)

    current_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.dirname(current_dir))
    
    from logic.FPGACodec import (
        HWCodec, 
        HWConfig
    )
    from comms.HWGateway import HWGateway

    global_gateway = None
    global_di_state = 0
    global_soe_log = "Init..."

    class WebMockDispatcher:
        def HandleHardwareRaw(self, rawData: bytes):
            global global_di_state, global_soe_log
            timeStamp, statusCode = HWCodec.ParseFeedbackFrame(rawData)
            
            if statusCode is not None:
                if statusCode == HWCodec.ACK_SUCCESS:
                    pass
                elif statusCode == HWCodec.ERR_ILLEGAL:
                    print(f"[HW] ERR_ILLEGAL @ {timeStamp}")
                else:
                    new_state = statusCode & 0xFF
                    old_state = (statusCode >> 8) & 0xFF
                    global_di_state = new_state
                    
                    time_ms = timeStamp / 1000.0
                    log_line = f"[{time_ms:.1f}ms] DI: {old_state:08b} -> {new_state:08b}"
                    print(f"[SOE] {log_line}")
                    
                    global_soe_log = log_line + "\n" + global_soe_log
                    if len(global_soe_log) > 500:
                        global_soe_log = global_soe_log[:500]

    async def web_connect(port_name: str):
        global global_gateway
        if global_gateway:
            return "[Warn] Gateway exists"
        
        global_gateway = HWGateway(port=port_name, baudrate=115200)
        global_gateway.dispatcher = WebMockDispatcher()
        
        asyncio.create_task(global_gateway.Connect())
        
        await asyncio.sleep(0.5)
        return f"[OK] Connected: {port_name}"

    async def webSendQuick(frame_name: str):
        global global_gateway
        if not global_gateway:
            return "[Err] Not connected"
        try:
            frame = getattr(HWCodec, frame_name)
            global_gateway.SendBytes(frame)
            return f"[OK] Sent {frame_name}: {frame.hex().upper()}"
        except Exception as e:
            return f"[Err] {e}"

    async def webSend4bCustom(cmd_str: str, dbnc_ms: float, booleans: list):
        global global_gateway
        if not global_gateway:
            return "[Err] Not connected"
        try:
            cmd_val = getattr(HWCodec, cmd_str)
            if cmd_val == HWCodec.SYS_SET_DBNC:
                safe_db = max(0.0, min(float(dbnc_ms), HWConfig.MAX_DBNC))
                u8_val = HWConfig.ConvertDbncToReg(safe_db)
                debug_info = f"Dbn: {safe_db:.2f}ms"
            else:
                u8_val = 0
                if booleans:
                    for b_str in booleans:
                        b_idx = int(b_str.replace("B", ""))
                        u8_val |= (1 << b_idx)
                debug_info = f"Mask [{','.join(booleans)}]"
                    
            frame = HWCodec.BuildSystemFrame(cmd_val, u8_val & 0xFF)
            global_gateway.SendBytes(frame)
            return f"[OK] {cmd_str} ({u8_val} | {debug_info}) -> {frame.hex().upper()}"
        except Exception as e:
            return f"[Err] {e}"

    async def webSend12bCustom(cmd_str: str, layer_idx: float, isCurrent: bool, ch_list: list, ampVal: float, paramVal: float):
        global global_gateway
        if not global_gateway:
            return "[Err] Not connected"
        try:
            cmd_val = getattr(HWCodec, cmd_str)
            l_idx = int(layer_idx)
            
            mask = 0
            if ch_list:
                for ch_str in ch_list:
                    ch_idx = int(ch_str.replace("CH", ""))
                    mask |= (1 << ch_idx)
                
            ampVal = float(ampVal)
            paramVal = float(paramVal)
            
            maxAmp = HWConfig.MAX_CURRENT if isCurrent else HWConfig.MAX_VOLTAGE
            safeAmp = max(-maxAmp, min(ampVal, maxAmp))
            a_reg = HWConfig.ConvertAmpToReg(safeAmp, isCurrent)
            
            if l_idx == 255:
                safeParam = min(paramVal, HWConfig.MAX_FREQ)
                p_reg = HWConfig.ConvertFreqToReg(safeParam) & 0xFFFFFFFF
            else:
                safeParam = min(paramVal, 360.0)
                p_reg = HWConfig.ConvertPhaseToReg(safeParam) & 0xFFFFFFFF
            
            frame = HWCodec.BuildParamFrame(cmd_val, l_idx, mask, a_reg, p_reg)
            global_gateway.SendBytes(frame)
            
            return f"[OK] Wave L{l_idx} | Amp:{safeAmp:.2f} Param:{safeParam:.2f} -> {frame.hex().upper()}"
        except Exception as e:
            return f"[Err] {e}"

    def buildApp():
        dds_cmds = [k for k in dir(HWCodec)  if k.startswith('DDS_')]
        
        quick_frames = [
            "FRAME_SYS_START", "FRAME_SYS_STOP", "FRAME_SYS_PAUSE",
            "FRAME_SYS_STANDBY", "FRAME_SYS_RESET", "FRAME_SYS_UPDATE"
        ]
        
        def makeQuickHandler(f_name):
            async def handler():
                return await webSendQuick(f_name)
            return handler
            
        def make4bHandler(cmd_name):
            async def handler(dbnc_ms, booleans):
                return await webSend4bCustom(cmd_name, dbnc_ms, booleans)
            return handler
            
        with gr.Blocks(title="FPGA Test Panel") as app:
            gr.Markdown("## FPGA Test Panel")
            
            with gr.Row():
                default_p = 'COM3' if os.name == 'nt' else '/dev/ttyAMA0'
                port_input = gr.Textbox(label="Port", value=default_p, scale=2)
                btn_conn = gr.Button("Connect", variant="primary", scale=1)
                text_status = gr.Textbox(label="Status", interactive=False, scale=3)
                
                btn_conn.click(fn=web_connect, inputs=[port_input], outputs=[text_status])
            
            global_res = gr.Textbox(label="Logs", interactive=False)
            gr.Markdown("---")
            
            with gr.Row():
                
                with gr.Column(scale=1):
                    gr.Markdown("### Sys Cmds (4-Byte)")
                    with gr.Group():
                        for f_name in quick_frames:
                            title = f_name.replace("FRAME_SYS_", "")
                            btn = gr.Button(title)
                            btn.click(fn=makeQuickHandler(f_name), outputs=[global_res])
                    
                    gr.Markdown("<br/>### DI Feedback")
                    with gr.Group():
                        chk_di = gr.CheckboxGroup(choices=[f"DI{i}" for i in range(8)], label="DI State", interactive=False)
                        txt_soe = gr.Textbox(label="SOE Logs", lines=6, interactive=False)
                
                with gr.Column(scale=3):
                    gr.Markdown("### DDS Config (12-Byte)")
                    with gr.Group():
                        with gr.Row():
                            drp_12b = gr.Dropdown(choices=dds_cmds, label="CMD", value="DDS_WR_LOAD", scale=2)
                            num_idx = gr.Number(label="Layer (255=Base)", value=255, precision=0, step=1, scale=2)
                        
                        chk_is_curr = gr.Checkbox(label="Is Current?")
                        
                        ch_options = [f"CH{i}" for i in range(16)]
                        chk_ch = gr.CheckboxGroup(choices=ch_options, label="Channel", value=["CH0"])
                        
                        with gr.Row():
                            txt_amp = gr.Number(label="Amp (V/A)", value=50.0)
                            txt_param = gr.Number(label="Param (Hz/Deg)", value=50.0)
                            
                        btn_send_12b = gr.Button("Send 12-Byte", variant="primary")
                        btn_send_12b.click(fn=webSend12bCustom, inputs=[drp_12b, num_idx, chk_is_curr, chk_ch, txt_amp, txt_param], outputs=[global_res])

                with gr.Column(scale=1):
                    gr.Markdown("### Param Cmds (4-Byte)")
                    
                    with gr.Group():
                        num_dbnc = gr.Number(label="Debounce (ms)", value=20.0, step=0.1)
                        btn_set_dbnc = gr.Button("SET DBNC", variant="secondary")
                        
                    with gr.Group():
                        chk_bool8 = gr.CheckboxGroup(choices=[f"B{i}" for i in range(8)], label="Mask")
                        btn_set_do = gr.Button("SET DO")

                    btn_set_dbnc.click(fn=make4bHandler("SYS_SET_DBNC"), inputs=[num_dbnc, chk_bool8], outputs=[global_res])
                    btn_set_do.click(fn=make4bHandler("SYS_SET_DO"), inputs=[num_dbnc, chk_bool8], outputs=[global_res])
                    
            def pollDi():
                import time
                while True:
                    time.sleep(0.5)
                    global global_di_state, global_soe_log
                    selected = [f"DI{i}" for i in range(8) if (global_di_state & (1 << i))]
                    yield selected, global_soe_log
                
            app.load(fn=pollDi, inputs=None, outputs=[chk_di, txt_soe])

        return app

    print("[System] Starting WebUI...")
    app = buildApp()
    app.launch(server_name="0.0.0.0", server_port=7860, show_error=True, theme=gr.themes.Monochrome())
