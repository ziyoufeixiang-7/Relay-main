"""
Frame Capture Test Harness
==========================
用于替代真实串口进行 TX 帧捕获与对比测试。
通过 Monkey-Patch HWGateway 的连接和发送函数，
让测试无需真实硬件即可运行并记录所有 TX 帧。
"""
import asyncio
import comms.HWGateway as _gw_mod
from logic.FPGACodec import HWCodec


class FrameCapture:
    """捕获所有 TX 帧并模拟 ACK 响应"""
    
    def __init__(self):
        self.txFrames = []
        self._patched = False
    
    def patch(self):
        """Monkey-patch HWGateway 使其不需要真实串口"""
        if self._patched:
            return
        
        capture = self
        
        async def fakeConnect(selfGw):
            """模拟连接成功，不打开真实串口"""
            selfGw.logger.info(f"[MOCK] Connected to {selfGw.port}")
            while True:
                await asyncio.sleep(3600)
        
        def fakeSend(selfGw, byteData: bytes):
            """捕获所有 TX 帧"""
            hexStr = byteData.hex().upper()
            capture.txFrames.append(hexStr)
            
            # 模拟 ACK — 直接解析 engine 的 ackFuture
            engine = selfGw.engine
            if engine and engine.ackFuture and not engine.ackFuture.done():
                engine.ackFuture.set_result(12345)
        
        _gw_mod.HWGateway.Connect = fakeConnect
        _gw_mod.HWGateway.SendBytes = fakeSend
        self._patched = True
    
    def Reset(self):
        self.txFrames.clear()
