import os
import subprocess
import tempfile
import uuid

def run_cmd(cmd, check=True):
    """Run a shell command and return its stdout."""
    res = subprocess.run(cmd, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and res.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}\nStdout: {res.stdout}\nStderr: {res.stderr}")
    return res.stdout.strip()

class LoopDevice:
    def __init__(self, size_mb=100):
        self.size_mb = size_mb
        self.file_path = None
        self.loop_dev = None

    def setup(self):
        # Create a backing file
        fd, self.file_path = tempfile.mkstemp(prefix="dm_xor_test_")
        os.close(fd)
        run_cmd(f"dd if=/dev/zero of={self.file_path} bs=1M count={self.size_mb} status=none")
        
        # Setup loop device
        self.loop_dev = run_cmd(f"sudo losetup -f --show {self.file_path}")
        return self.loop_dev

    def teardown(self):
        if self.loop_dev:
            run_cmd(f"sudo losetup -d {self.loop_dev}", check=False)
        if self.file_path and os.path.exists(self.file_path):
            os.remove(self.file_path)

class DMTarget:
    def __init__(self, name, table):
        self.name = name
        self.table = table
        self.dev_path = f"/dev/mapper/{name}"

    def setup(self):
        # Pass table directly via stdin to dmsetup to avoid shell echo -e issues
        cmd = f"sudo dmsetup create {self.name}"
        res = subprocess.run(cmd, shell=True, text=True, input=self.table, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            raise RuntimeError(f"Command failed: {cmd}\nStdout: {res.stdout}\nStderr: {res.stderr}\nTable: {self.table}")
        # Wait for udev to create the device node
        run_cmd("sudo udevadm settle", check=False)
        return self.dev_path

    def teardown(self):
        if os.path.exists(self.dev_path) or run_cmd(f"sudo dmsetup status {self.name}", check=False):
            run_cmd(f"sudo dmsetup remove {self.name}", check=False)
            run_cmd("sudo udevadm settle", check=False)

def get_dm_xor_table(devs, chunk_size=4096):
    """
    Generate a device-mapper table for dm-xor.
    Format: <start> <length> xor <dev1> <dev2> ...
    """
    if not devs:
        raise ValueError("At least one device required")
    
    # We assume all devices have the same size. We find the size in sectors (512 bytes).
    size_bytes = int(run_cmd(f"sudo blockdev --getsize64 {devs[0]}"))
    size_sectors = size_bytes // 512
    
    table = f"0 {size_sectors} xor "
    dev_args = " ".join(devs)
    return table + dev_args
