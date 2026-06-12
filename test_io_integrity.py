import os
import hashlib
from utils.dm_helpers import run_cmd

def test_basic_read_write(dm_xor, tmp_path):
    """Test that data written to the dm-xor target can be read back correctly."""
    xor_dev, backing_devs = dm_xor(dev_count=2, size_mb=50)
    
    # Generate random data
    test_file = tmp_path / "test_data.bin"
    run_cmd(f"dd if=/dev/urandom of={test_file} bs=1M count=10 status=none")
    
    # Calculate checksum of original data
    orig_md5 = run_cmd(f"md5sum {test_file} | awk '{{print $1}}'")
    
    # Write to dm-xor device
    run_cmd(f"sudo dd if={test_file} of={xor_dev} bs=1M status=none")
    
    # Read back from dm-xor device
    read_file = tmp_path / "read_data.bin"
    run_cmd(f"sudo dd if={xor_dev} of={read_file} bs=1M count=10 status=none")
    
    # Calculate checksum of read data
    read_md5 = run_cmd(f"md5sum {read_file} | awk '{{print $1}}'")
    
    assert orig_md5 == read_md5, "Data integrity verification failed: read data does not match written data."

def test_filesystem_creation(dm_xor, tmp_path):
    """Test that a filesystem can be created and mounted on the dm-xor target."""
    xor_dev, backing_devs = dm_xor(dev_count=3, size_mb=100)
    
    # Format the device with ext4
    run_cmd(f"sudo mkfs.ext4 -F {xor_dev}")
    
    # Create mount point
    mnt_dir = tmp_path / "mnt"
    mnt_dir.mkdir()
    
    try:
        # Mount the device
        run_cmd(f"sudo mount {xor_dev} {mnt_dir}")
        
        # Write a file
        test_file = mnt_dir / "hello.txt"
        run_cmd(f"sudo bash -c 'echo \"Hello DM-XOR!\" > {test_file}'")
        
        # Unmount and remount
        run_cmd(f"sudo umount {mnt_dir}")
        run_cmd(f"sudo mount {xor_dev} {mnt_dir}")
        
        # Read the file
        content = run_cmd(f"sudo cat {test_file}")
        assert content == "Hello DM-XOR!", "Filesystem data persistence failed."
    finally:
        run_cmd(f"sudo umount {mnt_dir}", check=False)
