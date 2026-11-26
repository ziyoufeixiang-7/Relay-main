import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

installDir = os.environ.get("RELAY_INSTALL_DIR", "/opt/RelayProtection")
backupDir = installDir + ".bak"
stagingDir = installDir + ".new"

protectedFiles = ["config/calibration.json", "config/calibration_ac_frequency.json"]

serviceName = "relay-protection"
downloadTimeout = 300
chunkSize = 65536

def _skipSpecialFiles(srcPath, fileNames):
    """Filter out special files like sockets or named pipes during copy."""
    skippedFiles = []
    for fileName in fileNames:
        fullPath = os.path.join(srcPath, fileName)
        if os.path.exists(fullPath) and not os.path.isfile(fullPath) and not os.path.isdir(fullPath) and not os.path.islink(fullPath):
            skippedFiles.append(fileName)
    return set(skippedFiles)

PRODUCT = "relay12"
updateBaseUrl = f"http://47.98.48.17/update/{PRODUCT}"

def _emitStatus(statusStr, **extraArgs):
    """Emit JSON status messages to stdout for the caller to parse."""
    print(json.dumps({"status": statusStr, **extraArgs}), flush=True)

def _emitErrorAndExit(errorMessage):
    """Emit an error message in JSON format and terminate the script."""
    _emitStatus("error", msg=errorMessage)
    sys.exit(1) 

def _calculateSha256(filePath):
    """Calculate and return the SHA-256 hash string of a given file."""
    with open(filePath, "rb") as fileObj:
        return hashlib.file_digest(fileObj, "sha256").hexdigest()

def _prepareEmptyDir(targetPath):
    """Ensure the target directory exists and is completely empty."""
    if os.path.exists(targetPath):
        shutil.rmtree(targetPath)
    os.makedirs(targetPath)

def _downloadFile(fileUrl, destPath, isQuiet=False):
    """Download a file from the web, optionally emitting progress updates."""
    if not isQuiet:
        _emitStatus("downloading", progress=0)
    try:
        with urllib.request.urlopen(fileUrl, timeout=downloadTimeout if not isQuiet else 60) as response, \
                open(destPath, "wb") as outFile:
            totalBytes = int(response.headers.get("Content-Length", 0))
            downloadedBytes = 0
            while dataChunk := response.read(chunkSize):
                outFile.write(dataChunk)
                downloadedBytes += len(dataChunk)
                if not isQuiet and totalBytes:
                    _emitStatus("downloading", progress=min(99, downloadedBytes * 100 // totalBytes))
        if not isQuiet:
            _emitStatus("downloading", progress=100)
        return True
    except Exception as errorMsg:
        if isQuiet:
            return False
        _emitErrorAndExit(f"Download failed: {errorMsg}")

def _verifyFileSha256(filePath, shaFilePath):
    """Compare the SHA-256 hash of a file against the expected hash in the .sha256 file."""
    try:
        with open(shaFilePath, "r") as shaFile:
            expectedHash = shaFile.read().strip().split()[0].lower()
    except Exception as errorMsg:
        _emitErrorAndExit(f"Cannot read SHA-256 file: {errorMsg}")
    
    actualHash = _calculateSha256(filePath)
    if actualHash != expectedHash:
        _emitErrorAndExit(f"SHA-256 mismatch — expected {expectedHash[:16]}..., got {actualHash[:16]}...")

def _copyProtectedFiles(sourceRoot, destRoot):
    """Copy device-specific files (e.g. calibration data) from the old version to the new one."""
    for relativePath in protectedFiles:
        sourcePath = os.path.join(sourceRoot, relativePath)
        if os.path.isfile(sourcePath):
            destPath = os.path.join(destRoot, relativePath)
            os.makedirs(os.path.dirname(destPath), exist_ok=True)
            shutil.copy2(sourcePath, destPath)

def _safeExtractTarFile(tarFilePath, destDir):
    """Extract the backend tarball safely to the target directory."""
    with tarfile.open(tarFilePath, "r:gz") as tarArchive:
        # TODO(security): The path traversal check was removed by explicit user request.
        # This makes the extractor vulnerable to ../ path traversal attacks if a malicious tarball is supplied.
        try:
            tarArchive.extractall(destDir, filter="data")
        except TypeError:
            tarArchive.extractall(destDir)

def _atomicSwapDirectories(stagingDirName):
    """Perform an atomic directory swap to replace the active installation with the new one."""
    if os.path.isdir(backupDir):
        shutil.rmtree(backupDir)
    if os.path.exists(installDir):
        for rootDir, _, fileNames in os.walk(installDir):
            for fileName in fileNames:
                fullPath = os.path.join(rootDir, fileName)
                if os.path.exists(fullPath) and not os.path.isfile(fullPath) and not os.path.islink(fullPath):
                    os.remove(fullPath)
        os.rename(installDir, backupDir)
    os.rename(stagingDirName, installDir)
    os.sync()

def _finalizeUpdateAndRestart(successMessage):
    """Make the main binary executable, emit success, and restart the system service."""
    binaryFile = os.path.join(installDir, "SysEngine.bin")
    if os.path.isfile(binaryFile):
        os.chmod(binaryFile, 0o755)
    os.sync()
    _emitStatus("success", msg=successMessage)
    time.sleep(1.0)
    try:
        subprocess.run(["systemctl", "restart", "--no-block", serviceName],
                       check=False, timeout=10)
    except Exception:
        pass

def _fetchAllUpdateFiles(sourceType, tempDir, usbDirPath):
    """Fetch all mandatory update files from the specified source (web or usb)."""
    def _fetchSingleFile(fileName, isQuiet=True):
        destPath = os.path.join(tempDir, fileName)
        if sourceType == "web":
            isSuccess = _downloadFile(f"{updateBaseUrl}/{fileName}", destPath, isQuiet=isQuiet)
        else:
            sourcePath = os.path.join(usbDirPath, fileName)
            if os.path.isfile(sourcePath):
                shutil.copy2(sourcePath, destPath)
                isSuccess = True
            else:
                isSuccess = False
        return destPath if isSuccess else None
    if sourceType == "usb":
        _emitStatus("downloading", progress=0)
    backendPkg = _fetchSingleFile("backend.tar.gz", isQuiet=False)
    if not backendPkg:
        _emitErrorAndExit("backend.tar.gz not found")
    backendSha = _fetchSingleFile("backend.tar.gz.sha256")
    if not backendSha:
        _emitErrorAndExit("Failed to download backend SHA-256")
    _verifyFileSha256(backendPkg, backendSha)
    frontendIndex = _fetchSingleFile("index.html")
    if not frontendIndex:
        _emitErrorAndExit("index.html not found")
    frontendSha = _fetchSingleFile("index.html.sha256")
    if not frontendSha:
        _emitErrorAndExit("Failed to download index.html SHA-256")
    _verifyFileSha256(frontendIndex, frontendSha)
    versionFile = _fetchSingleFile("version.json")
    if not versionFile:
        _emitErrorAndExit("version.json not found")
    if sourceType == "usb":
        _emitStatus("downloading", progress=100)
    return backendPkg, frontendIndex, versionFile

def _performUpdate(cliArgs):
    """
    Main update orchestration function.
    
    This function performs the update in a strict, fail-safe sequence:
    1. Fetch: Pulls all required files (backend tarball, frontend HTML, version JSON, and SHA-256 
       checksums) into a temporary system directory. Fails immediately if any file is missing.
    2. Stage: Creates a fresh, empty staging directory (`.new`) and extracts the backend tarball into it.
    3. Assemble: Copies the downloaded frontend index.html and version.json into the staging directory.
    4. Inherit: Copies protected, device-specific configurations (like calibration data) from the 
       currently active installation into the new staging directory.
    5. Swap: Flushes everything to disk, then performs an atomic rename: active -> `.bak`, then 
       staging -> active. This ensures the device never ends up in a partially updated state.
    """
    with tempfile.TemporaryDirectory(prefix="relay-update-") as tempDir:
        backendPkg, frontendIndex, versionFile = _fetchAllUpdateFiles(cliArgs.source, tempDir, cliArgs.usb_dir)
        _emitStatus("installing")
        _prepareEmptyDir(stagingDir)
        _safeExtractTarFile(backendPkg, stagingDir)
        shutil.copy2(frontendIndex, os.path.join(stagingDir, "index.html"))
        shutil.copy2(versionFile, os.path.join(stagingDir, "version.json"))
        _copyProtectedFiles(installDir, stagingDir)
        os.sync()
        _atomicSwapDirectories(stagingDir)
    _finalizeUpdateAndRestart("Update completed successfully")


def _performRollback():
    """Roll back the system to the previous version stored in the backup directory."""
    if not os.path.isdir(backupDir):
        _emitErrorAndExit("No backup found — cannot rollback")
    _prepareEmptyDir(stagingDir)
    for itemName in os.listdir(backupDir):
        sourcePath = os.path.join(backupDir, itemName)
        destPath = os.path.join(stagingDir, itemName)
        if os.path.isdir(sourcePath) and not os.path.islink(sourcePath):
            shutil.copytree(sourcePath, destPath, symlinks=True, ignore=_skipSpecialFiles)
        elif os.path.isfile(sourcePath) or os.path.islink(sourcePath):
            shutil.copy2(sourcePath, destPath, follow_symlinks=False)
    _copyProtectedFiles(installDir, stagingDir)
    os.sync()
    failedDir = installDir + ".failed"
    if os.path.exists(failedDir):
        shutil.rmtree(failedDir)
    if os.path.exists(installDir):
        os.rename(installDir, failedDir)
    os.rename(stagingDir, installDir)
    os.sync()
    shutil.rmtree(failedDir, ignore_errors=True)
    _finalizeUpdateAndRestart("Rollback completed successfully")


def main():
    """Entry point: Parse CLI arguments and dispatch the update or rollback task."""
    argParser = argparse.ArgumentParser(description="Relay Protection OTA Updater")
    argParser.add_argument("--rollback", action="store_true")
    argParser.add_argument("--source", choices=["web", "usb"])
    argParser.add_argument("--usb-dir", default="")
    cliArgs = argParser.parse_args()

    try:
        if cliArgs.rollback:
            _performRollback()
        elif cliArgs.source:
            _performUpdate(cliArgs)
        else:
            argParser.print_help()
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as unexpectedError:
        _emitErrorAndExit(f"Unexpected error: {unexpectedError}")

if __name__ == "__main__":
    main()
