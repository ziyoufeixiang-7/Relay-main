#!/usr/bin/env python3
"""
SYS_SYNC 延迟测试工具
发送 1000 次 SYS_SYNC 并测量每次的 ACK 往返延迟。
"""
import serial
import struct
import time
import sys

PORT = '/dev/ttyAMA0'
BAUDRATE = 230400
ROUNDS = 10000
TIMEOUT = 0.1  # 100ms 超时

# SYS_SYNC 帧: [0x5A, 0x06, 0x00, XOR]
FRAME_SYS_SYNC  = bytes([0x5A, 0x06, 0x00, 0x5A ^ 0x06])
FRAME_SYS_RESET = bytes([0x5A, 0x04, 0x00, 0x5A ^ 0x04])
FRAME_SYS_START = bytes([0x5A, 0x00, 0x00, 0x5A ^ 0x00])
SYNC_BYTE = 0x55

def read_ack(ser):
    """读取一个 ACK 帧: 找 0x55 同步头 → 读 6 字节"""
    # 找同步字节
    while True:
        b = ser.read(1)
        if len(b) == 0:
            return None  # 超时
        if b[0] == SYNC_BYTE:
            break
    # 读 6 字节负载
    data = ser.read(6)
    if len(data) < 6:
        return None
    timestamp, status = struct.unpack('<IH', data)
    return timestamp, status

def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else ROUNDS

    print(f"=== SYS_SYNC 延迟测试 ===")
    print(f"串口: {PORT} @ {BAUDRATE}")
    print(f"测试轮数: {rounds}")
    print()

    ser = serial.Serial(PORT, BAUDRATE, timeout=TIMEOUT)
    ser.reset_input_buffer()

    # 先 RESET + START 初始化硬件
    print("[INIT] 发送 SYS_RESET...")
    ser.write(FRAME_SYS_RESET)
    time.sleep(0.05)
    ser.reset_input_buffer()
    
    print("[INIT] 发送 SYS_START...")
    ser.write(FRAME_SYS_START)
    ack = read_ack(ser)
    if ack is None:
        print("[ERR] START 无 ACK 返回，检查硬件连接！")
        ser.close()
        return
    print(f"[INIT] START ACK: ts={ack[0]}, status=0x{ack[1]:04X}")
    
    # 清空可能的 DI 初始事件
    time.sleep(0.05)
    ser.reset_input_buffer()
    print()

    # 开始测试
    latencies_us = []
    errors = 0
    timeouts = 0

    print(f"[TEST] 开始发送 {rounds} 次 SYS_SYNC...")
    total_start = time.perf_counter()

    for i in range(rounds):
        t0 = time.perf_counter()
        ser.write(FRAME_SYS_SYNC)
        ack = read_ack(ser)
        t1 = time.perf_counter()

        if ack is None:
            timeouts += 1
            continue

        ts, status = ack
        if status == 0x0000:
            latencies_us.append((t1 - t0) * 1e6)
        elif status == 0xFFFF:
            errors += 1
        else:
            # DI 事件，跳过不计
            pass

    total_elapsed = time.perf_counter() - total_start

    # 复位硬件
    ser.write(FRAME_SYS_RESET)
    ser.close()

    # 统计结果
    print()
    print(f"=== 测试结果 ===")
    print(f"总耗时:     {total_elapsed*1000:.1f} ms")
    print(f"成功:       {len(latencies_us)}/{rounds}")
    print(f"超时:       {timeouts}")
    print(f"错误:       {errors}")
    print()

    if latencies_us:
        latencies_us.sort()
        avg = sum(latencies_us) / len(latencies_us)
        p50 = latencies_us[len(latencies_us) // 2]
        p95 = latencies_us[int(len(latencies_us) * 0.95)]
        p99 = latencies_us[int(len(latencies_us) * 0.99)]
        mn = latencies_us[0]
        mx = latencies_us[-1]

        print(f"平均延迟:   {avg:.1f} µs")
        print(f"最小延迟:   {mn:.1f} µs")
        print(f"中位 P50:   {p50:.1f} µs")
        print(f"P95:        {p95:.1f} µs")
        print(f"P99:        {p99:.1f} µs")
        print(f"最大延迟:   {mx:.1f} µs")
        print(f"吞吐量:     {len(latencies_us)/total_elapsed:.0f} 帧/秒")

if __name__ == '__main__':
    main()
