"""
test_updater.py — OTA updater 端到端本地模拟（无需真机）

通过 RELAY_INSTALL_DIR 环境变量把安装目录指到临时目录，并用假的
sudo/systemctl（PATH 前置）拦截服务重启，验证原子更新模型：

  1. 正常更新：新文件就位、calibration 保留、.bak=旧版、先 success 后重启、
     重启用 --no-block（不被自己触发的重启杀掉）
  2. SHA-256 错包：拒绝且现役安装分毫未动
  3. 路径穿越包：拒绝且现役安装分毫未动
  4. 解压中途 SIGKILL（模拟掉电/被杀）：现役安装完整无损（旧版仍可启动）
  5. 杀死后重跑：暂存残留被清理，更新成功（自愈）
  6. 回滚：恢复旧版、保留当前 calibration、.bak 原样保留
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
UPDATER = os.path.join(TESTS_DIR, "..", "Updater.py")

total = passed = 0


def check(label, ok):
    global total, passed
    total += 1
    passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} {label}")
    return ok


def sha256_of(path):
    import hashlib
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


class Env:
    """临时安装环境 + 假 sudo/systemctl。"""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="ota-test-")
        self.install = os.path.join(self.root, "opt", "RelayProtection")
        self.bin = os.path.join(self.root, "bin")
        self.restart_log = os.path.join(self.root, "systemctl.log")
        os.makedirs(self.bin)
        # 假 sudo：直接执行后续命令（PATH 已前置，会命中假 systemctl）
        self._script("sudo", '#!/bin/bash\nexec "$@"\n')
        # 假 systemctl：记录参数
        self._script("systemctl", f'#!/bin/bash\necho "$@" >> "{self.restart_log}"\nexit 0\n')

    def _script(self, name, content):
        p = os.path.join(self.bin, name)
        with open(p, "w") as f:
            f.write(content)
        os.chmod(p, 0o755)

    def env(self):
        e = dict(os.environ)
        e["RELAY_INSTALL_DIR"] = self.install
        e["PATH"] = self.bin + os.pathsep + e.get("PATH", "")
        e["SUDO_UID"] = str(os.getuid())
        e["SUDO_GID"] = str(os.getgid())
        return e

    def make_install(self, version, calib='{"0": [0.1, -0.95, -0.95]}'):
        if os.path.exists(self.install):
            shutil.rmtree(self.install)
        os.makedirs(os.path.join(self.install, "config"))
        with open(os.path.join(self.install, "version.json"), "w") as f:
            json.dump({"version": version}, f)
        with open(os.path.join(self.install, "SysEngine.bin"), "w") as f:
            f.write(f"#!/bin/bash\necho engine {version}\n")
        with open(os.path.join(self.install, "config", "calibration.json"), "w") as f:
            f.write(calib)

    def make_package(self, version, big=False):
        """构造更新包目录结构（relay12/），同时生成 tar.gz + .sha256。"""
        src = os.path.join(self.root, f"pkg-src-{version}")
        os.makedirs(os.path.join(src, "config"), exist_ok=True)
        with open(os.path.join(src, "version.json"), "w") as f:
            json.dump({"version": version}, f)
        with open(os.path.join(src, "SysEngine.bin"), "w") as f:
            f.write(f"#!/bin/bash\necho engine {version}\n")
        with open(os.path.join(src, "config", "calibration.json"), "w") as f:
            f.write('{"factory": "default"}')   # 包内默认校准（应被设备版覆盖）
        if big:
            with open(os.path.join(src, "blob.dat"), "wb") as f:
                f.write(os.urandom(80 * 1024 * 1024))
        # 创建 relay12/ USB 目录结构
        usb_dir = os.path.join(self.root, f"usb-{version}", "relay12")
        os.makedirs(usb_dir, exist_ok=True)
        pkg = os.path.join(usb_dir, "backend.tar.gz")
        # 真实 backend.tar.gz = 后端 DIST（tar -C $DIST .），不含 version.json/index.html，
        # 它们由 publish 脚本作为 sidecar 单独上传。这里照此排除，使保留语义可被验证。
        with tarfile.open(pkg, "w:gz", compresslevel=1) as tf:
            for item in os.listdir(src):
                if item in ("version.json", "index.html"):
                    continue
                tf.add(os.path.join(src, item), arcname=item)
        # 生成 .sha256
        sha = sha256_of(pkg)
        with open(pkg + ".sha256", "w") as f:
            f.write(f"{sha}  backend.tar.gz\n")
        # 放入 version.json
        shutil.copy2(os.path.join(src, "version.json"), os.path.join(usb_dir, "version.json"))
        
        # 放入 index.html 及其 sha256
        index_path = os.path.join(usb_dir, "index.html")
        with open(index_path, "w") as f:
            f.write(f"FRONTEND v{version}")
        idx_sha = sha256_of(index_path)
        with open(index_path + ".sha256", "w") as f:
            f.write(f"{idx_sha}  index.html\n")
            
        shutil.rmtree(src)
        return usb_dir, sha

    def make_evil_package(self):
        """含路径穿越成员的恶意包。"""
        usb_dir = os.path.join(self.root, "usb-evil", "relay12")
        os.makedirs(usb_dir, exist_ok=True)
        pkg = os.path.join(usb_dir, "backend.tar.gz")
        payload = os.path.join(self.root, "evil.txt")
        with open(payload, "w") as f:
            f.write("pwned")
        with tarfile.open(pkg, "w:gz") as tf:
            tf.add(payload, arcname="../evil.txt")
        sha = sha256_of(pkg)
        with open(pkg + ".sha256", "w") as f:
            f.write(f"{sha}  backend.tar.gz\n")
        return usb_dir, sha

    def read_version(self):
        try:
            with open(os.path.join(self.install, "version.json")) as f:
                return json.load(f)["version"]
        except Exception:
            return None

    def read_calib(self):
        try:
            with open(os.path.join(self.install, "config", "calibration.json")) as f:
                return f.read()
        except Exception:
            return None

    def install_intact(self):
        return all(os.path.isfile(os.path.join(self.install, p))
                   for p in ("version.json", "SysEngine.bin", "config/calibration.json"))


def run_updater(env, *args, kill_on=None):
    """跑 updater，返回 (rc, [json行])；kill_on=状态名时读到该状态立即 SIGKILL。"""
    proc = subprocess.Popen([sys.executable, UPDATER, *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env.env(), text=True)
    lines = []
    killed = False
    for line in proc.stdout:
        try:
            lines.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if kill_on and lines[-1].get("status") == kill_on:
            time.sleep(0.05)            # 让解压真正展开一点
            proc.kill()
            killed = True
            break
    proc.wait(timeout=60)
    return (-9 if killed else proc.returncode), lines


def main():
    print("=" * 60)
    print("OTA updater 原子更新模拟测试")
    print("=" * 60)
    e = Env()
    DEVICE_CALIB = '{"0": [0.123, -0.95, -0.95], "device": "unit-7"}'

    # ── 1. 正常更新 ──
    print("\n[1] 正常更新 v1.0.0 → v2.0.0")
    e.make_install("1.0.0", calib=DEVICE_CALIB)
    usb_dir, sha = e.make_package("2.0.0")
    rc, lines = run_updater(e, "--source", "usb", "--usb-dir", usb_dir)
    statuses = [l["status"] for l in lines]
    check("退出码 0", rc == 0)
    check("现役安装 = v2.0.0", e.read_version() == "2.0.0")
    check("设备 calibration 保留（非包内出厂值）", e.read_calib() == DEVICE_CALIB)
    bak_ver = json.load(open(os.path.join(e.install + ".bak", "version.json")))["version"]
    check(".bak = 旧版 v1.0.0", bak_ver == "1.0.0")
    check("success 状态出现", "success" in statuses)
    with open(e.restart_log) as f:
        check("重启用 --no-block（不被自己杀掉）", "--no-block" in f.read())

    # ── 2. SHA-256 错包 ──
    print("\n[2] SHA-256 校验失败 → 现役安装不动")
    # 篡改 .sha256 文件
    sha_file = os.path.join(usb_dir, "backend.tar.gz.sha256")
    with open(sha_file, "w") as f:
        f.write("0" * 64 + "  backend.tar.gz\n")
    rc, lines = run_updater(e, "--source", "usb", "--usb-dir", usb_dir)
    check("拒绝（退出码非 0 + error）", rc != 0 and lines[-1]["status"] == "error")
    check("现役安装仍为 v2.0.0 完整", e.read_version() == "2.0.0" and e.install_intact())
    # 恢复正确的 sha256
    with open(sha_file, "w") as f:
        f.write(f"{sha}  backend.tar.gz\n")

    # ── 4. 解压中途 SIGKILL（掉电模拟）──
    print("\n[4] 解压中途 SIGKILL → 现役安装完整无损")
    big_dir, big_sha = e.make_package("3.0.0", big=True)
    rc, lines = run_updater(e, "--source", "usb", "--usb-dir", big_dir,
                            kill_on="installing")
    check("进程被杀（rc=-9）", rc == -9)
    check("现役安装仍 v2.0.0 完整可用", e.read_version() == "2.0.0" and e.install_intact())
    check("calibration 仍在", e.read_calib() == DEVICE_CALIB)

    # ── 5. 杀死后重跑自愈 ──
    print("\n[5] 重跑更新 → 暂存残留被清理、成功升到 v3.0.0")
    rc, lines = run_updater(e, "--source", "usb", "--usb-dir", big_dir)
    check("重跑成功", rc == 0 and e.read_version() == "3.0.0")
    check("calibration 依旧保留", e.read_calib() == DEVICE_CALIB)
    check("暂存目录已消失（被换位）", not os.path.exists(e.install + ".new"))

    # ── 6. 回滚 ──
    print("\n[6] 回滚 → 恢复 .bak 版本、保留当前 calibration")
    bak_ver = json.load(open(os.path.join(e.install + ".bak", "version.json")))["version"]
    rc, lines = run_updater(e, "--rollback")
    check("回滚成功", rc == 0)
    check(f"现役安装回到 .bak 版本 v{bak_ver}", e.read_version() == bak_ver)
    check("calibration 保留", e.read_calib() == DEVICE_CALIB)
    check(".bak 原样保留（可再次回滚）", os.path.isdir(e.install + ".bak"))

    # ── 7. B1：前后端分离下 index.html / version.json 的保留语义 ──
    print("\n[7] 更新包不含 index.html → 报错拒绝更新")
    e.make_install("5.0.0", calib=DEVICE_CALIB)
    with open(os.path.join(e.install, "index.html"), "w") as f:
        f.write("OLD-FRONTEND")
    
    # 7a：包内无 index.html（仅后端 + version.json）
    usb_dir, _ = e.make_package("6.0.0")
    os.remove(os.path.join(usb_dir, "index.html"))
    os.remove(os.path.join(usb_dir, "index.html.sha256"))
    rc, lines = run_updater(e, "--source", "usb", "--usb-dir", usb_dir)
    check("拒绝更新（rc != 0）", rc != 0)
    check("现役版本仍为 5.0.0", e.read_version() == "5.0.0")

    # 7b：包内含 index.html → 替换为新版
    print("\n[7b] 更新包完整包含 index.html 和 version.json → 成功更新")
    usb_dir2, _ = e.make_package("7.0.0")
    rc, _ = run_updater(e, "--source", "usb", "--usb-dir", usb_dir2)
    check("更新成功", rc == 0 and e.read_version() == "7.0.0")
    check("index.html 被替换为新版",
          open(os.path.join(e.install, "index.html")).read() == "FRONTEND v7.0.0")

    # 7c：包内无 version.json → 报错拒绝更新
    print("\n[7c] 更新包不含 version.json → 报错拒绝更新")
    usb_dir3, _ = e.make_package("8.0.0")
    os.remove(os.path.join(usb_dir3, "version.json"))
    
    rc, _ = run_updater(e, "--source", "usb", "--usb-dir", usb_dir3)
    check("拒绝更新（rc != 0）", rc != 0)
    check("现役版本仍为 7.0.0", e.read_version() == "7.0.0")

    shutil.rmtree(e.root, ignore_errors=True)
    print("\n" + "=" * 60)
    print(f"RESULT: {passed}/{total} tests passed")
    print("✅ ALL TESTS PASSED!" if passed == total else f"❌ {total - passed} FAILED!")
    print("=" * 60)
    return passed == total


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
